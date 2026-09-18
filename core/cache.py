"""Распределённый кэш на базе Redis с поддержкой TTL и in-memory fallback.

Защита от «Thundering Herd» (эффект стада): при промахе кэша все параллельные
запросы одновременно идут вычислять одно и то же значение — база получает
шквал одинаковых тяжёлых запросов ровно в момент истечения TTL. Функция
``cache_get_or_set`` ставит на ключ вычисления per-key мьютекс: значение
считает один запрос, остальные ждут и читают результат из кэша.
"""

import asyncio
import inspect
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from core.config import settings
from core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

# Fallback хранилище: ключ -> (время_истечения_epoch, значение)
_memory_cache: dict[str, tuple[float, Any]] = {}
_MAX_MEMORY_ENTRIES = 1000
_CLEANUP_INTERVAL = 60.0
_last_cleanup = 0.0

# Реестр мьютексов вычисления: (id(event loop), ключ кэша) -> asyncio.Lock.
# Цикл событий входит в ключ, потому что asyncio.Lock привязывается к loop,
# а тесты и перезапуски воркеров создают новые циклы.
_lock_registry: dict[tuple[int, str], asyncio.Lock] = {}
_LOCK_REGISTRY_LIMIT = 4096


def _cleanup_memory_cache(force: bool = False) -> None:
    """Удалить просроченные ключи из in-memory fallback.

    Выполняется не чаще раза в минуту или при превышении лимита размера,
    чтобы не делать O(N) перебор словаря при каждом чтении/записи.
    """
    global _last_cleanup
    now = time.time()
    if (
        not force
        and len(_memory_cache) < _MAX_MEMORY_ENTRIES
        and (now - _last_cleanup) < _CLEANUP_INTERVAL
    ):
        return
    _last_cleanup = now
    expired = [k for k, (exp, _) in _memory_cache.items() if exp <= now]
    for k in expired:
        _memory_cache.pop(k, None)


# --- Защита от Thundering Herd: мьютекс на вычисление значения ключа -------


def _prune_lock_registry(current_loop_id: int) -> None:
    """Выкинуть мьютексы чужих (закрытых) циклов событий."""
    stale = [registry_key for registry_key in _lock_registry if registry_key[0] != current_loop_id]
    for registry_key in stale:
        _lock_registry.pop(registry_key, None)


def _key_lock(key: str) -> asyncio.Lock:
    """Мьютекс вычисления значения ключа для текущего цикла событий."""
    loop_id = id(asyncio.get_running_loop())
    registry_key = (loop_id, key)
    lock = _lock_registry.get(registry_key)
    if lock is None:
        if len(_lock_registry) >= _LOCK_REGISTRY_LIMIT:
            _prune_lock_registry(loop_id)
            if len(_lock_registry) >= _LOCK_REGISTRY_LIMIT:
                _lock_registry.clear()
        lock = asyncio.Lock()
        _lock_registry[registry_key] = lock
    return lock


def _release_key_lock(key: str, lock: asyncio.Lock) -> None:
    """Убрать освободившийся мьютекс (реестр не должен расти бесконечно)."""
    registry_key = (id(asyncio.get_running_loop()), key)
    if not lock.locked() and _lock_registry.get(registry_key) is lock:
        _lock_registry.pop(registry_key, None)


@asynccontextmanager
async def cache_compute_lock(key: str) -> AsyncIterator[None]:
    """Контекст-менеджер: эксклюзивное вычисление значения для ключа кэша.

    Нужен, когда «промах» обрабатывается на стороне вызывающего кода
    (например, значение кэшируется не через ``cache_set`` этого модуля).
    """
    lock = _key_lock(key)
    try:
        async with lock:
            yield
    finally:
        _release_key_lock(key, lock)


async def _call_factory(factory: Callable[[], Any]) -> Any:
    """Вызвать factory: async-функция — в цикле событий, sync — в пуле потоков."""
    if inspect.iscoroutinefunction(factory):
        return await factory()
    result = await asyncio.to_thread(factory)
    if inspect.isawaitable(result):
        # Callable-объект с асинхронным __call__: корутину дочитываем в loop.
        return await result
    return result


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
    deleted_keys = set()
    redis = await get_redis_client()
    if redis is not None:
        try:
            if hasattr(redis, "scan_iter"):
                keys: list[str] = []
                async for k in redis.scan_iter(match=f"cache:{pattern}", count=100):
                    keys.append(k)
                    if len(keys) >= 500:
                        await redis.delete(*keys)
                        deleted_keys.update(k.removeprefix("cache:") for k in keys)
                        keys.clear()
                if keys:
                    await redis.delete(*keys)
                    deleted_keys.update(k.removeprefix("cache:") for k in keys)
            else:
                keys = await redis.keys(f"cache:{pattern}")
                if keys:
                    await redis.delete(*keys)
                    deleted_keys.update(k.removeprefix("cache:") for k in keys)
        except Exception as e:
            logger.debug("Redis cache_delete_pattern failed (%s): %s", pattern, e)

    # In-memory fallback
    import fnmatch

    to_remove = [k for k in _memory_cache if fnmatch.fnmatch(k, pattern)]
    for k in to_remove:
        _memory_cache.pop(k, None)
        deleted_keys.add(k)

    return len(deleted_keys)


async def cache_get_or_set(
    key: str,
    factory: Callable[[], Any],
    ttl: int = settings.CACHE_DEFAULT_TTL,
) -> Any:
    """Вернуть значение из кэша, вычислив его ровно один раз при промахе.

    Thundering Herd: без мьютекса N параллельных запросов с одинаковым
    промахом одновременно вызывают ``factory`` (тяжёлый SQL/HTTP), и база
    получает всплеск нагрузки сразу после истечения TTL. Здесь вычисление
    выполняется под per-key мьютексом, остальные участники «стаи» ждут его
    и забирают готовое значение из кэша (double-check под локом).

    ``factory`` — async-функция (вызывается в цикле событий) или обычная
    (вызывается в пуле потоков через asyncio.to_thread).
    Значение ``None`` не кэшируется: оно означает промах.
    """
    cached = await cache_get(key)
    if cached is not None:
        return cached

    lock = _key_lock(key)
    try:
        async with lock:
            # Double-check: пока ждали мьютекс, значение уже мог вычислить
            # «лидер» стаи.
            cached = await cache_get(key)
            if cached is not None:
                return cached
            value = await _call_factory(factory)
            if value is not None:
                await cache_set(key, value, ttl=ttl)
            return value
    finally:
        _release_key_lock(key, lock)
