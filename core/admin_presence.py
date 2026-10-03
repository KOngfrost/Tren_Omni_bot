"""
Индикатор активности администраторов (Онлайн / Офлайн / Истёк).

Идея: каждый авторизованный запрос к веб-панели «пишет» в Redis ключ
``admin:heartbeat:{web_user_id}`` с TTL = ADMIN_PRESENCE_TTL_SECONDS
(по умолчанию 120 секунд). Пока ключ жив — администратор считается онлайн.

Почему Redis, а не только БД:
- обновление не требует записи в БД на каждый запрос (иначе таблица web_users
  превращается в горячую точку при нескольких воркерах uvicorn);
- состояние сразу видно всем воркерам и репликам веб-панели;
- TTL гарантирует самопроизвольный «оффлайн» без фонового сборщика.

TTL намеренно короткий: индикатор должен отражать текущую активность,
а не «заходил в течение четверти часа». Значение настраивается через
ADMIN_PRESENCE_TTL_SECONDS в .env.

При недоступности Redis используется внутрипроцессный fallback с тем же TTL,
чтобы индикатор не молчал: показатели собираются из БД (last_login_at),
а «онлайн» просто останется пустым.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.config import settings
from core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

# TTL присутствия. Берётся из конфигурации, чтобы его можно было менять
# без правки кода; жёстко зашитое значение быстро устаревает.
ADMIN_PRESENCE_TTL_SECONDS = max(30, int(settings.ADMIN_PRESENCE_TTL_SECONDS))

# Префикс ключа Redis. Единый источник правды для писателя (dependencies.py)
# и читателей (admin_panel.py, app_metrics.py).
ADMIN_HEARTBEAT_PREFIX = "admin:heartbeat:"

# Fallback на случай недоступности Redis: {web_user_id: expiry_monotonic}.
_memory_presence: dict[int, float] = {}


def heartbeat_key(web_user_id: int) -> str:
    """Ключ Redis для heartbeat конкретного веб-пользователя."""
    return f"{ADMIN_HEARTBEAT_PREFIX}{web_user_id}"


def _prune_memory(now: float) -> None:
    """Убрать из fallback-хранилища истёкшие записи."""
    for key, expiry in list(_memory_presence.items()):
        if expiry <= now:
            _memory_presence.pop(key, None)


async def touch_presence(web_user_id: int, ttl: int = ADMIN_PRESENCE_TTL_SECONDS) -> bool:
    """Отметить администратора как активного прямо сейчас.

    Возвращает True, если присутствие зафиксировано (Redis или fallback).
    Вызывается на каждый авторизованный запрос, поэтому не должен бросать
    исключений: сбой отслеживания не должен ломать работу панели.
    """
    if not web_user_id:
        return False

    redis = await get_redis_client()
    if redis is not None:
        try:
            await redis.set(heartbeat_key(web_user_id), str(int(time.time())), ex=ttl)
            return True
        except Exception as exc:  # pragma: no cover - зависит от состояния Redis
            logger.debug("Не удалось записать heartbeat в Redis: %s", exc)

    now = time.monotonic()
    _prune_memory(now)
    _memory_presence[web_user_id] = now + ttl
    return True


async def get_online_ids() -> set[int]:
    """Множество web_user_id администраторов, активных за последние TTL секунд.

    Возвращает пустое множество, если Redis недоступен: молчаливый «все офлайн»
    хуже честной пустоты — вызывающий код решает, как это отобразить.
    """
    redis = await get_redis_client()
    if redis is not None:
        try:
            found: set[int] = set()
            async for key in redis.scan_iter(match=f"{ADMIN_HEARTBEAT_PREFIX}*", count=100):
                raw_id = str(key).removeprefix(ADMIN_HEARTBEAT_PREFIX)
                if raw_id.isdigit():
                    found.add(int(raw_id))
            return found
        except Exception as exc:  # pragma: no cover - зависит от состояния Redis
            logger.debug("Не удалось прочитать heartbeat из Redis: %s", exc)
            return set()

    now = time.monotonic()
    _prune_memory(now)
    return set(_memory_presence)


async def is_online(web_user_id: int) -> bool:
    """Активен ли конкретный администратор в данный момент."""
    if not web_user_id:
        return False
    return web_user_id in await get_online_ids()


async def count_online() -> int:
    """Количество администраторов онлайн (для /metrics в Telegram)."""
    return len(await get_online_ids())


async def clear_presence(web_user_id: int) -> None:
    """Убрать отметку о присутствии (например, при выходе из панели)."""
    if not web_user_id:
        return
    _memory_presence.pop(web_user_id, None)
    redis: Any = await get_redis_client()
    if redis is None:
        return
    try:
        await redis.delete(heartbeat_key(web_user_id))
    except Exception as exc:  # pragma: no cover - зависит от состояния Redis
        logger.debug("Не удалось удалить heartbeat из Redis: %s", exc)


async def revoke_all_active_sessions() -> int:
    """Принудительно завершить все активные сессии администраторов.

    1. Находит и удаляет все ключи `session:*` в Redis.
    2. Удаляет все heartbeat-ключи `admin:heartbeat:*` в Redis.
    3. Очищает локальный in-memory словарь присутствия.

    Возвращает количество завершённых сессий.
    """
    mem_count = len(_memory_presence)
    _memory_presence.clear()
    total_revoked = mem_count

    redis: Any = await get_redis_client()
    if redis is not None:
        try:
            session_keys: list[str] = [
                key async for key in redis.scan_iter(match="session:*", count=100)
            ]
            if session_keys:
                await redis.delete(*session_keys)
                total_revoked += len(session_keys)

            hb_keys: list[str] = [
                key async for key in redis.scan_iter(match=f"{ADMIN_HEARTBEAT_PREFIX}*", count=100)
            ]
            if hb_keys:
                await redis.delete(*hb_keys)
                total_revoked += len(hb_keys)

            logger.warning(
                "Все активные сессии администраторов принудительно завершены: удалено %d записей",
                total_revoked,
            )
        except Exception as exc:
            logger.error("Ошибка при отзыве сессий в Redis: %s", exc)

    return total_revoked


__all__ = [
    "ADMIN_HEARTBEAT_PREFIX",
    "ADMIN_PRESENCE_TTL_SECONDS",
    "clear_presence",
    "count_online",
    "get_online_ids",
    "heartbeat_key",
    "is_online",
    "revoke_all_active_sessions",
    "touch_presence",
]
