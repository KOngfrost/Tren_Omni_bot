"""
Индикатор активности администраторов (Онлайн / Офлайн / Истёк).

Идея: каждый авторизованный запрос к веб-панели «пишет» в Redis ключ
``admin:heartbeat:{web_user_id}`` с TTL = ADMIN_PRESENCE_TTL_SECONDS (15 минут).
Пока ключ жив — администратор считается онлайн.

Почему Redis, а не только БД:
- обновление не требует записи в БД на каждый запрос (иначе таблица web_users
  превращается в горячую точку при нескольких воркерах uvicorn);
- состояние сразу видно всем воркерам и репликам веб-панели;
- TTL гарантирует самопроизвольный «оффлайн» без фонового сборщика.

При недоступности Redis используется внутрипроцессный fallback с тем же TTL,
чтобы индикатор не молчал: показатели собираются из БД (last_login_at),
а «онлайн» просто останется пустым.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

# 15 минут — как указано в ТЗ: администратор считается онлайн, пока он активен
# в панели в течение последних 15 минут.
ADMIN_PRESENCE_TTL_SECONDS = 15 * 60

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
    """Множество web_user_id администраторов, активных за последние 15 минут.

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


__all__ = [
    "ADMIN_HEARTBEAT_PREFIX",
    "ADMIN_PRESENCE_TTL_SECONDS",
    "clear_presence",
    "count_online",
    "get_online_ids",
    "heartbeat_key",
    "is_online",
    "touch_presence",
]
