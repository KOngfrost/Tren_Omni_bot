"""Redis-backed сессионное middleware для распределённого хранения сессий с TTL."""

import base64
import hashlib
import json
import logging
import secrets
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, Signer
from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.config import settings
from core.redis_client import get_redis_client

logger = logging.getLogger(__name__)


def _derive_fernet_key(secret_key: str) -> bytes:
    """Детерминированно вывести 32-байтный Fernet-ключ из секрета сессий.

    Fernet требует ключ в виде url-safe Base64 от ровно 32 байт; секрет
    приложения произвольной длины сворачивается SHA-256.
    """
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class RedisSessionMiddleware:
    """Распределённое хранилище сессий на базе Redis.

    - При доступном Redis: хранит состояние сессии в Redis по ключу `session:{session_id}`
      с установкой TTL (по умолчанию 24 часа), а в cookie клиента помещает подписанный ID сессии.
      Любой воркер Uvicorn может прозрачно читать и изменять сессию.
    - При недоступном Redis (или локальном запуске без Redis): автоматически переключается
      на cookie-сессию с локальной сериализацией, обеспечивая zero-downtime.
      SEC-09: данные fallback-сессии перед записью в cookie шифруются
      симметричным шифрованием (Fernet, AES-128-CBC + HMAC-SHA256) поверх
      подписи itsdangerous.Signer — содержимое сессии (ID пользователя,
      отделы, роли) больше не читается из открытого Base64.
    """

    def __init__(
        self,
        app: ASGIApp,
        secret_key: str,
        session_cookie: str = "session",
        max_age: int | None = None,
        path: str = "/",
        same_site: str = "lax",
        https_only: bool = False,
    ) -> None:
        self.app = app
        self.signer = Signer(secret_key)
        self.fernet = Fernet(_derive_fernet_key(secret_key))
        self.session_cookie = session_cookie
        self.max_age = max_age or settings.SESSION_TTL
        self.path = path
        self.security_flags = f"path={path}; samesite={same_site}"
        if https_only:
            self.security_flags += "; secure"
        self.security_flags += "; httponly"

    def _decrypt_session_payload(self, token: str) -> dict[str, Any]:
        """SEC-09: расшифровать данные fallback-cookie-сессии (Fernet).

        Возвращает {} для недействительных/устаревших токенов —
        повреждённая сессия приравнивается к отсутствующей.
        """
        try:
            return json.loads(self.fernet.decrypt(token.encode("utf-8")))
        except InvalidToken:
            logger.debug("Не удалось расшифровать cookie-сессию (недействительный токен)")
        except (TypeError, ValueError):
            logger.debug("Некорректный формат расшифрованной cookie-сессии")
        return {}

    async def _load_session(
        self, raw_cookie: str | None, redis: Any
    ) -> tuple[str | None, dict[str, Any]]:
        if not raw_cookie:
            return None, {}
        try:
            unsigned = self.signer.unsign(raw_cookie.encode("utf-8")).decode("utf-8")
            if unsigned.startswith("s_"):
                session_id = unsigned[2:]
                if redis is not None:
                    val = await redis.get(f"session:{session_id}")
                    if val:
                        return session_id, json.loads(val)
                return session_id, {}
            # SEC-09: fallback-cookie содержит зашифрованные (Fernet) данные сессии.
            return None, self._decrypt_session_payload(unsigned)
        except (BadSignature, Exception) as exc:
            logger.debug("Не удалось восстановить сессию: %s", exc)
            return None, {}

    async def _persist_session(
        self, current_session: dict[str, Any], session_id: str | None, redis: Any
    ) -> tuple[str, str | None]:
        cookie_val = ""
        if redis is not None:
            if not session_id:
                session_id = secrets.token_urlsafe(32)
            serialized = json.dumps(current_session, ensure_ascii=False, default=str)
            try:
                await redis.set(f"session:{session_id}", serialized, ex=self.max_age)
                cookie_val = self.signer.sign(f"s_{session_id}".encode()).decode("utf-8")
            except Exception as e:
                logger.warning("Ошибка записи сессии в Redis, fallback на cookie: %s", e)
                session_id = None

        if not cookie_val:
            # SEC-09: данные fallback-сессии шифруются Fernet перед подписью —
            # из cookie невозможно прочитать ID пользователя и отделы.
            encrypted = self.fernet.encrypt(
                json.dumps(current_session, ensure_ascii=False, default=str).encode("utf-8")
            ).decode("utf-8")
            cookie_val = self.signer.sign(encrypted.encode("utf-8")).decode("utf-8")

        return cookie_val, session_id

    async def _refresh_session_ttl(self, session_id: str | None, redis: Any) -> None:
        """Ошибка #8 — скользящее продление сессии (sliding expiration).

        При каждом авторизованном входящем запросе обновляем TTL ключа сессии
        в Redis (EXPIRE session:{id}), предотвращая принудительное разлогинивание
        активно работающего администратора: таймаут отсчитывается от последней
        активности, а не от момента входа.
        """
        if redis is None or not session_id:
            return
        try:
            await redis.expire(f"session:{session_id}", self.max_age)
        except Exception as e:
            logger.debug("Не удалось продлить TTL сессии %s: %s", session_id, e)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        connection = HTTPConnection(scope)
        raw_cookie = connection.cookies.get(self.session_cookie)
        redis = await get_redis_client()
        session_id, data = await self._load_session(raw_cookie, redis)

        # Sliding expiration: продлеваем TTL только для живых сессий
        # (с данными) при каждом входящем запросе.
        if session_id and data:
            await self._refresh_session_ttl(session_id, redis)

        scope["session"] = data
        initial_hash = hash(json.dumps(data, sort_keys=True, default=str))

        async def send_wrapper(message: Message) -> None:
            nonlocal session_id
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                current_session = scope.get("session", {})

                if not current_session and (raw_cookie or session_id):
                    if redis is not None and session_id:
                        try:
                            await redis.delete(f"session:{session_id}")
                        except Exception as e:
                            logger.debug("Не удалось удалить сессию из Redis: %s", e)
                    headers.append(
                        "Set-Cookie",
                        f"{self.session_cookie}=; max-age=0; expires=Thu, 01 Jan 1970 00:00:00 GMT; {self.security_flags}",
                    )
                elif current_session and (
                    hash(json.dumps(current_session, sort_keys=True, default=str)) != initial_hash
                    or not raw_cookie
                ):
                    # Защита от Session Fixation: при входе пользователя
                    # (появление ключа "user" в сессии) или флаге session_rotate
                    # генерируем новый session_id и удаляем старый ключ из Redis.
                    was_authenticated = bool(data.get("user"))
                    now_authenticated = bool(current_session.get("user"))
                    should_rotate = (not was_authenticated and now_authenticated) or scope.get("session_rotate")

                    if should_rotate and session_id and redis is not None:
                        try:
                            await redis.delete(f"session:{session_id}")
                        except Exception as e:
                            logger.debug("Не удалось удалить старую сессию при ротации: %s", e)
                        session_id = None

                    cookie_val, session_id = await self._persist_session(
                        current_session, session_id, redis
                    )
                    cookie_header = (
                        f"{self.session_cookie}={cookie_val}; "
                        f"max-age={self.max_age}; {self.security_flags}"
                    )
                    headers.append("Set-Cookie", cookie_header)

            await send(message)

        await self.app(scope, receive, send_wrapper)
