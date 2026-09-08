
"""Выделенный фоновый диспетчер: гарантия «ровно один исполнитель».

Проблема: при масштабировании веб-панели на несколько uvicorn-воркеров
(WEB_WORKERS=4, Dockerfile.web) периодические фоновые задачи запускаются
в нескольких процессах одновременно. Claim-семантика outbox
(``with_for_update(skip_locked=True)``) не даёт отправить сообщение дважды,
но воркеры напрасно конкурируют за блокировки и нагружают БД опросами.

Решение без новой инфраструктуры (TaskIQ/Celery требуют брокера — Redis
или RabbitMQ — и отдельного сервиса в docker-compose): PostgreSQL
advisory lock.

Периодическая задача оборачивается в ``single_instance_guard()``:
- процесс, первым захвативший advisory lock, выполняет партию;
- остальные процессы за этот тик ничего не делают;
- замок живёт на выделенном соединении только пока выполняется работа;
  при падении лидера соединение закрывается, замок освобождается, и на
  следующем тике его занимает другой процесс — отказоустойчиво.

На SQLite (dev/тесты) guard всегда возвращает True: там один процесс.

Миграция на TaskIQ/Celery (при росте нагрузки): заменить запуск
``outbox_worker_loop()`` в main.py и web/main.py на брокерную задачу,
выполняющую ``deliver_pending_messages()`` — она уже идемпотентна
благодаря claim-семантике.
"""

import logging
import zlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text

import core.database as database_module

logger = logging.getLogger(__name__)


def _lock_key(lock_name: str) -> int:
    """Стабильный bigint-ключ advisory lock из имени задачи."""
    return zlib.crc32(lock_name.encode("utf-8")) & 0x7FFFFFFFFFFFFFFF


@asynccontextmanager
async def single_instance_guard(lock_name: str) -> AsyncIterator[bool]:
    """Выполнить блок кода только в одном процессе (PostgreSQL advisory lock).

    Yields True, если текущий процесс стал исполнителем (замок захвачен),
    False — если эту задачу уже выполняет другой процесс.

    Замок session-level: живёт на выделенном соединении до явного
    pg_advisory_unlock или закрытия соединения (переживает commit, в отличие
    от pg_advisory_xact_lock). Для не-PostgreSQL движков (SQLite в
    dev/тестах) замок не нужен — всегда True.
    """
    engine = database_module.engine

    conn = None
    acquired = False
    key = _lock_key(lock_name)

    try:
        if engine is None or engine.dialect.name != "postgresql":
            yield True
            return
        conn = await engine.connect()
        result = await conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        acquired = bool(result.scalar())
        # Завершаем неявную транзакцию: session-level замок при commit
        # НЕ снимается, а соединение остаётся чистым.
        await conn.commit()
        if not acquired:
            logger.debug("Диспетчер '%s': лидер — другой процесс", lock_name)
        yield acquired
    finally:
        if conn is not None:
            if acquired:
                try:
                    await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                    await conn.commit()
                except Exception:
                    logger.warning("Диспетчер '%s': не удалось снять advisory lock", lock_name)
            await conn.close()
