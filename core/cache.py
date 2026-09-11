"""Распределённый кэш на базе Redis с поддержкой TTL и in-memory fallback."""

import json
import logging
import time
from typing import Any

from core.config import settings
from core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

# Fallback хранилище: ключ -> (время_истечения_epoch, значение)
_memory_cache: dict[str, tuple[float, Any]] = {}


def _cleanup_memory_cache() -> None:
    """Удалить просроченные ключи из in-memory fallback."""
    now = time.time()
    expired = [k for k, (exp, _) in _memory_cache.items() if exp <= now]
    for k in expired:
        _memory_cache.pop(k, None)


async def cache_get(key: str) -> Any | None:
    """Получить значение из кэша.

    Если Redis доступен — читает из Redis.
    Иначе — читает из локальной памяти с проверкой TTL.
    """
    redis = await get_redis_client()
    if redis is not None:
        try:
            val = await redis.get(f"cache:{key}")
            if val is not None:
                return json.loads(val)
            return None
        except Exception as e:
            logger.debug("Redis cache_get failed (%s), fallback to memory: %s", key, e)

    # In-memory fallback
    _cleanup_memory_cache()
    entry = _memory_cache.get(key)
    if entry is not None:
        exp, val = entry
        if exp > time.time():
            return val
        _memory_cache.pop(key, None)
    return None


async def cache_set(
    key: str,
    value: Any,
    ttl: int = settings.CACHE_DEFAULT_TTL,
) -> bool:
    """Сохранить значение в кэш с TTL в секундах."""
    redis = await get_redis_client()
    if redis is not None:
        try:
            serialized = json.dumps(value, ensure_ascii=False, default=str)
            await redis.set(f"cache:{key}", serialized, ex=ttl)
            return True
        except Exception as e:
            logger.debug("Redis cache_set failed (%s), fallback to memory: %s", key, e)

    # In-memory fallback
    _cleanup_memory_cache()
    _memory_cache[key] = (time.time() + ttl, value)
    return True


async def cache_delete(key: str) -> bool:
    """Удалить ключ из кэша."""
    deleted = False
    redis = await get_redis_client()
    if redis is not None:
        try:
            res = await redis.delete(f"cache:{key}")
            deleted = bool(res)
        except Exception as e:
            logger.debug("Redis cache_delete failed (%s): %s", key, e)

    if key in _memory_cache:
        _memory_cache.pop(key, None)
        deleted = True
    return deleted


async def cache_delete_pattern(pattern: str) -> int:
    """Удалить все ключи по маске (например, 'dept_stats:*')."""
    total_deleted = 0
    redis = await get_redis_client()
    if redis is not None:
        try:
            if hasattr(redis, "scan_iter"):
                keys: list[str] = []
                async for k in redis.scan_iter(match=f"cache:{pattern}", count=100):
                    keys.append(k)
                    if len(keys) >= 500:
                        total_deleted += await redis.delete(*keys)
                        keys.clear()
                if keys:
                    total_deleted += await redis.delete(*keys)
            else:
                keys = await redis.keys(f"cache:{pattern}")
                if keys:
                    total_deleted = await redis.delete(*keys)
        except Exception as e:
            logger.debug("Redis cache_delete_pattern failed (%s): %s", pattern, e)

    # In-memory fallback
    import fnmatch

    to_remove = [k for k in _memory_cache if fnmatch.fnmatch(k, pattern)]
    for k in to_remove:
        _memory_cache.pop(k, None)
        total_deleted += 1

    return total_deleted
