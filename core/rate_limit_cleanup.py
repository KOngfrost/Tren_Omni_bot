"""Централизованная фоновая очистка устаревших записей rate-limit таблиц.

Переносит DELETE из горячего пути DBRateLimiter.is_allowed (crud_attempts)
и _record_failed_attempt (login_attempts) в единую периодическую задачу,
чтобы нагрузочный тест CRUD-операций не инициировал блокировок
таблиц попыток входа (Ошибка #10).

Запускается через start_rate_limit_cleanup() в планировщиках веб-панели
(web/main.py) и бота (main.py). Удаляет записи старше RETENTION_HOURS
(по умолчанию 24 ч) с периодом CLEANUP_INTERVAL_SECONDS (по умолчанию 1 ч).
"""

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from core.database import async_session_maker

logger = logging.getLogger(__name__)

# Период между запусками очистки (сек). По умолчанию 1 час; 1–6 часов
# покрывает требование задачи. Переокружить через env.
CLEANUP_INTERVAL_SECONDS = int(os.environ.get("RATE_LIMIT_CLEANUP_INTERVAL_SECONDS", "3600"))

# Возраст записей (часы), старше которого удаляем — те же 24 ч,
# что использовались в горячем пути.
RETENTION_HOURS = 24

# Централизованный список очищаемых таблиц: (имя таблицы, колонка со временем).
_CLEANUP_TABLES = (
    ("crud_attempts", "attempted_at"),
    ("login_attempts", "attempted_at"),
)


async def purge_stale_attempts(now: datetime | None = None) -> dict[str, int]:
    """Удалить записи старше RETENTION_HOURS из таблиц rate-limit.

    Возвращает {таблица: число удалённых строк}.
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(hours=RETENTION_HOURS)
    deleted: dict[str, int] = {}
    async with async_session_maker() as session:
        for table, ts_column in _CLEANUP_TABLES:
            try:
                result = await session.execute(
                    text(f"DELETE FROM {table} WHERE {ts_column} < :cutoff"),
                    {"cutoff": cutoff},
                )
                deleted[table] = getattr(result, "rowcount", 0)
                await session.commit()
            except Exception:
                await session.rollback()
                logger.warning("Rate-limit cleanup: ошибка очистки %s", table, exc_info=True)
                deleted[table] = 0
    return deleted


async def _cleanup_loop() -> None:
    """Бесконечный цикл периодической очистки устаревших записей."""
    from core.task_dispatcher import single_instance_guard

    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            async with single_instance_guard("rate-limit-cleanup") as is_leader:
                if is_leader:
                    deleted = await purge_stale_attempts()
                    if any(v > 0 for v in deleted.values()):
                        logger.info("Rate-limit cleanup завершена: %s", deleted)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Rate-limit cleanup: непредвиденная ошибка")


def start_rate_limit_cleanup() -> asyncio.Task:
    """Запускает фоновую очистку и возвращает asyncio.Task."""
    task = asyncio.create_task(_cleanup_loop())
    return task
