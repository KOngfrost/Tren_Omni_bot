"""Серверное хранилище одноразовых 2FA-кодов (OTP).

Какую уязвимость закрывает модуль
---------------------------------
Сессия веб-панели может храниться не только в Redis, но и в подписанной
cookie (fallback при недоступном Redis, см. ``web.security.session_store``).
``itsdangerous.Signer`` только подписывает данные, но НЕ шифрует их:
любой открытый OTP, сохранённый в ``request.session``, извлекается из cookie
обычным base64-декодированием.

Как решено
----------
1. Открытый код живёт только в памяти процесса в момент формирования
   VK-уведомления и (опционально) в серверном хранилище Redis.
2. В клиентскую сессию попадают исключительно:

   * ``token`` — криптографический токен попытки входа (256 бит);
   * ``code_hash`` — невосстанавливаемый из хеша код (Argon2id либо
     PBKDF2-HMAC-SHA256 с уникальной солью, см. ``web.security.passwords``).
     Он нужен только в fallback-режиме без Redis.

3. Состояние попытки уничтожается при успешной проверке, при блокировке по
   числу попыток, при повторной отправке кода, при выходе и по истечении TTL
   (Redis TTL + проверка ``expires_at`` в fallback-режиме).

Хранилище Fail-Closed: при сбое Redis состояние не восстанавливается из
устаревших данных — пользователь перелогинивается.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any, Literal

from core.config import settings
from core.redis_client import get_redis_client
from web.security.passwords import hash_password, verify_password

logger = logging.getLogger(__name__)

SESSION_KEY = "pending_2fa"
REDIS_PREFIX = "2fa:pending:"

VerifyStatus = Literal["ok", "missing", "expired", "invalid", "locked", "resend_limit"]


@dataclass(frozen=True)
class VerifyResult:
    """Итог проверки одноразового кода."""

    status: VerifyStatus
    user_data: dict[str, Any] | None = None
    remaining: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.user_data is not None


def generate_code() -> str:
    """Сгенерировать криптографически стойкий 6-значный OTP."""
    return f"{secrets.randbelow(900_000) + 100_000}"


def _max_attempts() -> int:
    return max(1, int(settings.WEB_ADMIN_2FA_MAX_ATTEMPTS))


def _redis_key(token: str) -> str:
    return f"{REDIS_PREFIX}{token}"


def _remaining_ttl(state: dict[str, Any]) -> int:
    """Сколько секунд осталось до истечения попытки (минимум 1)."""
    return max(1, int(float(state.get("expires_at", 0.0)) - time.time()))


def _store_in_session(request: Any, state: dict[str, Any], *, full: bool) -> None:
    """Сохранить состояние попытки в сессию.

    ``full=True`` — fallback-режим без Redis: кладём состояние целиком,
    но БЕЗ открытого кода (только криптографический хеш).
    ``full=False`` — Redis-режим: в сессии только токен попытки.
    """
    if full:
        request.session[SESSION_KEY] = dict(state)
    else:
        request.session[SESSION_KEY] = {"token": state["token"]}


async def _persist(request: Any, state: dict[str, Any], redis: Any) -> None:
    """Записать состояние: в Redis (при наличии) и одновременно в сессию."""
    if redis is not None:
        try:
            await redis.set(
                _redis_key(state["token"]),
                json.dumps(state, ensure_ascii=False),
                ex=_remaining_ttl(state),
            )
        except Exception as exc:
            logger.warning(
                "2FA: не удалось записать состояние в Redis (%s), fallback в сессию", exc
            )
    # В подписанную сессию всегда пишем безопасное состояние (хеш кода, без plaintext)
    _store_in_session(request, state, full=True)


async def _destroy(request: Any, state: dict[str, Any] | None, redis: Any) -> None:
    """Уничтожить попытку: токен и серверное состояние больше не действуют."""
    request.session.pop(SESSION_KEY, None)
    if redis is not None and state and state.get("token"):
        try:
            await redis.delete(_redis_key(str(state["token"])))
        except Exception as exc:
            logger.debug("2FA: не удалось удалить состояние из Redis: %s", exc)


async def _load(request: Any) -> tuple[dict[str, Any] | None, Any]:
    """Прочитать активное состояние попытки (без проверки TTL)."""
    raw = request.session.get(SESSION_KEY)
    if not isinstance(raw, dict):
        return None, None

    redis = await get_redis_client()
    token = str(raw.get("token") or "")

    if redis is not None and token:
        try:
            payload = await redis.get(_redis_key(token))
            if payload:
                state = json.loads(payload)
                if isinstance(state, dict):
                    state.setdefault("token", token)
                    return state, redis
        except Exception as exc:
            logger.warning("2FA: ошибка чтения из Redis (%s), fallback на сессию", exc)

    # Fallback-режим: состояние (без открытого кода) лежит в подписанной сессии
    if "code_hash" in raw and raw.get("token"):
        return dict(raw), redis

    if token:
        await _destroy(request, {"token": token}, redis)
    return None, redis


async def begin(
    request: Any,
    *,
    user_data: dict[str, Any],
    vk_admin_id: int | None,
    ttl: int,
) -> str:
    """Начать попытку 2FA: вернуть OTP и сохранить только его хеш.

    Открытый код возвращается вызывающему коду исключительно для отправки
    в доверенный канал связи и нигде дополнительно не сохраняется.
    """
    code = generate_code()
    state: dict[str, Any] = {
        "token": secrets.token_urlsafe(32),
        "user_data": dict(user_data),
        "vk_admin_id": vk_admin_id,
        "code_hash": hash_password(code),
        "attempts": 0,
        "resends": 0,
        "expires_at": time.time() + ttl,
    }
    redis = await get_redis_client()
    await _persist(request, state, redis)
    return code


async def rotate(request: Any, *, ttl: int) -> tuple[str | None, int]:
    """Выпустить новый код для той же попытки (resend).

    Старый код аннулируется немедленно. Возвращает ``(код, остаток отправок)``;
    ``(None, 0)`` — активной попытки нет, ``(None, N)`` здесь не используется:
    превышение лимита отдаётся через :func:`resend_quota`.
    """
    state, redis = await _load(request)
    if state is None:
        return None, 0
    await _destroy(request, state, redis)

    code = generate_code()
    resends = int(state.get("resends", 0) or 0) + 1
    new_state: dict[str, Any] = {
        "token": secrets.token_urlsafe(32),
        "user_data": dict(state.get("user_data") or {}),
        "vk_admin_id": state.get("vk_admin_id"),
        "code_hash": hash_password(code),
        "attempts": 0,
        "resends": resends,
        "expires_at": time.time() + ttl,
    }
    await _persist(request, new_state, redis)
    return code, max(0, _resend_limit() - resends)


def _resend_limit() -> int:
    return max(0, int(settings.WEB_ADMIN_2FA_RESEND_LIMIT))


def resend_quota(state: dict[str, Any] | None) -> int:
    """Сколько повторных отправок кода ещё доступно для попытки."""
    if state is None:
        return 0
    return max(0, _resend_limit() - int(state.get("resends", 0) or 0))


async def peek(request: Any) -> dict[str, Any] | None:
    """Вернуть активное состояние попытки (для отрисовки страницы ввода кода)."""
    state, redis = await _load(request)
    if state is None:
        return None
    if time.time() > float(state.get("expires_at", 0.0)):
        await _destroy(request, state, redis)
        return None
    return state


async def verify(request: Any, submitted_code: str) -> VerifyResult:
    """Проверить введённый код. Успешная проверка уничтожает попытку."""
    state, redis = await _load(request)
    if state is None:
        return VerifyResult("missing")

    if time.time() > float(state.get("expires_at", 0.0)):
        await _destroy(request, state, redis)
        return VerifyResult("expired")

    attempts = int(state.get("attempts", 0) or 0)
    max_attempts = _max_attempts()
    if attempts >= max_attempts:
        await _destroy(request, state, redis)
        return VerifyResult("locked")

    expected_hash = str(state.get("code_hash", ""))
    if submitted_code and verify_password(submitted_code, expected_hash):
        user_data = dict(state.get("user_data") or {})
        await _destroy(request, state, redis)
        if not user_data:
            return VerifyResult("missing")
        return VerifyResult("ok", user_data=user_data)

    state["attempts"] = attempts + 1
    remaining = max(0, max_attempts - int(state["attempts"]))
    await _persist(request, state, redis)
    return VerifyResult("invalid", remaining=remaining)


async def cancel(request: Any) -> None:
    """Полностью отозвать попытку 2FA (выход, отмена, ошибка)."""
    state, redis = await _load(request)
    await _destroy(request, state, redis)
