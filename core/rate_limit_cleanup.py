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

# DATA-001: Срок хранения записей системного аудита (дней)
AUDIT_LOG_RETENTION_DAYS = int(os.environ.get("AUDIT_LOG_RETENTION_DAYS", "90"))

# DATA-003: Срок хранения успешно доставленных сообщений Outbox (дней)
OUTBOX_SENT_RETENTION_DAYS = int(os.environ.get("OUTBOX_SENT_RETENTION_DAYS", "14"))

# Централизованный список очищаемых таблиц: (имя таблицы, колонка со временем).
_CLEANUP_TABLES = (
    ("crud_attempts", "attempted_at"),
    ("login_attempts", "attempted_at"),
)


async def purge_stale_attempts(now: datetime | None = None) -> dict[str, int]:
    """Удалить устаревшие записи из rate-limit таблиц, logs и vk_outbox.

    Возвращает {таблица: число удалённых строк}.
    """
    current_time = now or datetime.now(UTC)
    cutoff = current_time - timedelta(hours=RETENTION_HOURS)
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

        # DATA-001: Очистка старых логов аудита (>90 дней)
        try:
            log_cutoff = current_time - timedelta(days=AUDIT_LOG_RETENTION_DAYS)
            result = await session.execute(
                text("DELETE FROM logs WHERE created_at < :cutoff"),
                {"cutoff": log_cutoff},
            )
            deleted["logs"] = getattr(result, "rowcount", 0)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.warning("Retention cleanup: ошибка очистки logs", exc_info=True)
            deleted["logs"] = 0

        # DATA-003: Очистка доставленных сообщений Outbox (>14 дней)
        try:
            outbox_cutoff = current_time - timedelta(days=OUTBOX_SENT_RETENTION_DAYS)
            result = await session.execute(
                text("DELETE FROM vk_outbox WHERE status = 'sent' AND created_at < :cutoff"),
                {"cutoff": outbox_cutoff},
            )
            deleted["vk_outbox"] = getattr(result, "rowcount", 0)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.warning("Retention cleanup: ошибка очистки vk_outbox", exc_info=True)
            deleted["vk_outbox"] = 0

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
