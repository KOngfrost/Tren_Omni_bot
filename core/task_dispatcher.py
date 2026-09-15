"""Выделенный фоновый диспетчер: гарантия «ровно один исполнитель».

Проблема: при масштабировании веб-панели на несколько uvicorn-воркеров
(WEB_WORKERS=4, Dockerfile.web) периодические фоновые задачи запускаются
в нескольких процессах одновременно. Claim-семантика outbox
(``with_for_update(skip_locked=True)``) не даёт отправить сообщение дважды,
но воркеры напрасно конкурируют за блокировки и нагружают БД опросами.

Решение: распределённая блокировка на базе Redis (SET NX PX + Lua).

Почему не PostgreSQL advisory lock:
- PgBouncer работает в режиме transaction pooling: соединение «живёт» только
  на время транзакции (SERVER_RESET_QUERY=DISCARD ALL), поэтому session-level
  замок (pg_advisory_lock/unlock) не переживает commit и теряется при возврате
  соединения в пул — его неосознанно снимает чужой процесс.
- Держать отдельный постоянный пул прямых соединений к порту 5432 только
  ради замков — лишние соединения и дублирование конфигурации. Redis для
  сессий и кэша уже есть в стеке (docker-compose.yml, core/redis_client.py).

Реализация single_instance_guard():
- Acquire: ``SET distlock:<name> <token> NX PX <ttl>`` — атомарно и безопасно
  при конкурентном доступе нескольких процессов.
- Renew: фоновая asyncio-задача каждые TTL/3 продлевает TTL через Lua-скрипт,
  который проверяет владельца по токену (PEXPIRE).
- Release: Lua-скрипт удаляет ключ ТОЛЬКО если значение равно нашему токену
  (сравнение-и-удаление атомарны). Чужая блокировка не освобождается.
- Отказоустойчивость: если Redis недоступен (нет REDIS_URL / сеть упала),
  guard возвращает True — процесс работает «без блокировки». Outbox при этом
  не отправляет сообщение дважды благодаря claim-семантике; воркеры просто
  перестают координироваться до восстановления Redis.
- Если Redis-замок истёк до завершения работы (сеть/задержки), блокировку
  может захватить другой процесс — это ожидаемый trade-off TTL-замков;
  продление заметно чаще TTL сводит вероятность к нулю.

На SQLite (dev/тесты) Redis обычно не настроен: guard возвращает True.

Миграция на TaskIQ/Celery (при росте нагрузки): заменить запуск
outbox_worker_loop() в main.py и web/main.py на брокерную задачу,
выполняющую deliver_pending_messages() — она уже идемпотентна
благодаря claim-семантике.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from core.config import settings
from core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

# Ключи блокировок живут в отдельном namespace, чтобы не конфликтовать с кэшем.
_LOCK_KEY_PREFIX = "distlock:"

# Lua: освободить блокировку ТОЛЬКО если владелец — текущий процесс.
_RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

# Lua: продлить TTL ТОЛЬКО если владелец — текущий процесс.
_RENEW_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("pexpire", KEYS[1], ARGV[2])
else
    return 0
end
"""


def _lock_key(lock_name: str) -> str:
    """Стабильный ключ Redis из имени задачи."""
    return f"{_LOCK_KEY_PREFIX}{lock_name}"


def _ttl_ms() -> int:
    """TTL блокировки в миллисекундах (Redis PX)."""
    return max(int(settings.DISTRIBUTED_LOCK_TTL_SECONDS * 1000), 1000)


def _renew_interval_seconds() -> float:
    """Интервал автопродления: из настроек или TTL/3."""
    configured = getattr(settings, "DISTRIBUTED_LOCK_RENEW_INTERVAL_SECONDS", 0) or 0
    interval = configured if configured > 0 else settings.DISTRIBUTED_LOCK_TTL_SECONDS / 3.0
    return max(interval, 0.5)


async def _try_acquire(redis, key: str, token: str, lock_name: str) -> bool:
    """Попытаться атомарно захватить блокировку (SET NX PX)."""
    try:
        return bool(await redis.set(key, token, nx=True, px=_ttl_ms()))
    except Exception:
        # Redis временно недоступен: пропускаем блокировку, а не падаем.
        logger.warning("Диспетчер '%s': Redis недоступен, блокировка пропущена", lock_name)
        return True


async def _renew_loop(redis, key: str, token: str, lock_name: str) -> None:
    """Фоновая задача: продлевать TTL блокировки, пока держатель жив."""
    interval = _renew_interval_seconds()
    while True:
        await asyncio.sleep(interval)
        try:
            await redis.eval(_RENEW_LUA, 1, key, token, _ttl_ms())
        except Exception:
            # Redis временно недоступен: блокировка истечёт по TTL, и на
            # следующем тике её заберёт другой процесс — безопасно.
            logger.warning("Диспетчер '%s': не удалось продлить блокировку", lock_name)


async def _release(redis, key: str, token: str, lock_name: str) -> None:
    """Атомарно освободить блокировку (Lua compare-and-delete)."""
    try:
        await redis.eval(_RELEASE_LUA, 1, key, token)
    except Exception:
        logger.warning("Диспетчер '%s': не удалось снять блокировку", lock_name)


@asynccontextmanager
async def single_instance_guard(lock_name: str) -> AsyncIterator[bool]:
    """Выполнить блок кода только в одном процессе (Redis distributed lock).

    Yields True, если текущий процесс стал исполнителем (блокировка захвачена),
    False — если эту задачу уже выполняет другой процесс.

    Блокировка неблокирующая (non-blocking): если замок занят, guard сразу
    возвращает False, и процесс пропускает тик — как раньше с
    pg_try_advisory_lock. При недоступности Redis выполнение разрешается
    (fallback), см. докстроку модуля.
    """
    redis = None
    try:
        redis = await get_redis_client()
    except Exception:
        redis = None

    # Dev/тесты (SQLite) и отказ Redis: guard не мешает выполнению.
    if redis is None:
        logger.debug("Диспетчер '%s': Redis недоступен — выполняю без блокировки", lock_name)
        yield True
        return

    key = _lock_key(lock_name)
    token = uuid.uuid4().hex

    acquired = await _try_acquire(redis, key, token, lock_name)
    if not acquired:
        logger.debug("Диспетчер '%s': лидер — другой процесс", lock_name)
        yield False
        return

    renew_task = asyncio.create_task(_renew_loop(redis, key, token, lock_name))
    logger.debug("Диспетчер '%s': блокировка захвачена (token=%s)", lock_name, token[:8])
    try:
        yield True
    finally:
        renew_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await renew_task
        await _release(redis, key, token, lock_name)
