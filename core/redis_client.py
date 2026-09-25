"""Асинхронный клиент Redis с поддержкой пула соединений и graceful fallback."""

import contextlib
import logging
import re
import time
from typing import Any

from core.config import settings

logger = logging.getLogger(__name__)

_redis_client: Any = None
_redis_available: bool | None = None
_last_failed_attempt: float = 0.0
RECONNECT_INTERVAL_SECONDS = 30.0


def _mask_redis_url(url: str) -> str:
    if not url:
        return ""
    return re.sub(r"://([^:@]+:)?([^@]+)@", r"://\1***@", url)


async def get_redis_client() -> Any | None:
    """Получить глобальный асинхронный клиент Redis.

    Возвращает экземпляр redis.asyncio.Redis или None, если Redis недоступен.
    """
    global _redis_client, _redis_available, _last_failed_attempt

    if _redis_client is not None:
        return _redis_client

    if not settings.REDIS_URL:
        _redis_available = False
        return None

    now = time.time()
    if _redis_available is False and (now - _last_failed_attempt) < RECONNECT_INTERVAL_SECONDS:
        return None

    try:
        import redis.asyncio as aioredis  # type: ignore[import-untyped]

        client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            health_check_interval=30,
            max_connections=20,
        )
        # Проверяем подключение
        await client.ping()
        _redis_client = client
        _redis_available = True
        logger.info("Успешное подключение к Redis: %s", _mask_redis_url(settings.REDIS_URL))
        return _redis_client
    except Exception as e:
        logger.debug(
            "Redis недоступен (%s). Используется fallback: %s",
            _mask_redis_url(settings.REDIS_URL),
            e,
        )
        _redis_available = False
        _redis_client = None
        _last_failed_attempt = now
        return None


async def is_redis_available() -> bool:
    """Проверить доступность Redis."""
    global _redis_available, _redis_client
    if _redis_available is True:
        try:
            if _redis_client is not None:
                await _redis_client.ping()
                return True
        except Exception:
            _redis_client = None
            _redis_available = False
            return False

    client = await get_redis_client()
    return client is not None


async def close_redis_client() -> None:
    """Закрыть соединения с Redis при завершении приложения."""
    global _redis_client, _redis_available
    if _redis_client is not None:
        try:
            await _redis_client.close()
            logger.info("Соединение с Redis корректно закрыто")
        except Exception as e:
            logger.debug("Ошибка при закрытии Redis: %s", e)
        finally:
            _redis_client = None
            _redis_available = None
