"""Би-недельная архивация и сжатие старых логов и закрытых заявок.

Выполняет выгрузку старых заявок и логов аудита в сжатые JSON файлы (gzip)
и затем удаляет их из базы порциями, чтобы избежать блокировок таблиц.
"""

import asyncio
import gzip
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from core.database import async_session_maker
from core.models import Log, Ticket, TicketStatus

logger = logging.getLogger(__name__)

# Настройки архивации
ARCHIVE_TICKET_AGE_DAYS = int(os.environ.get("ARCHIVE_TICKET_AGE_DAYS", "90"))
ARCHIVE_LOG_AGE_DAYS = int(os.environ.get("ARCHIVE_LOG_AGE_DAYS", "90"))
ARCHIVE_INTERVAL_SECONDS = int(os.environ.get("ARCHIVE_INTERVAL_SECONDS", "1209600"))
ARCHIVE_OUTPUT_DIR = os.environ.get("ARCHIVE_OUTPUT_DIR", "archives/")

CHUNK_SIZE = 1000


async def archive_and_delete_tickets(now: datetime) -> tuple[int, str | None]:
    """Архивация и удаление старых выполненных заявок."""
    cutoff = now - timedelta(days=ARCHIVE_TICKET_AGE_DAYS)

    # Статусы для архивации
    statuses = [
        TicketStatus.COMPLETED,
        TicketStatus.COMPLETED_AUTO,
    ]

    total_archived = 0
    filepath = None

    Path(ARCHIVE_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    async with async_session_maker() as session:
        try:
            # Получаем все заявки, удовлетворяющие условию (фильтруем по updated_at)
            stmt = select(Ticket).where(
                Ticket.status.in_(statuses),
                Ticket.updated_at < cutoff,
            )
            result = await session.execute(stmt)
            tickets = list(result.scalars().all())

            if not tickets:
                return 0, None

            records = [
                {
                    "id": t.id,
                    "user_id": t.user_id,
                    "department_id": t.department_id,
                    "topic": t.topic,
                    "description": t.description,
                    "status": t.status.value if hasattr(t.status, "value") else str(t.status),
                    "is_anonymous": t.is_anonymous,
                    "response_text": t.response_text,
                    "created_at": t.created_at,
                    "updated_at": t.updated_at,
                }
                for t in tickets
            ]
            total_archived = len(records)

            timestamp_str = now.strftime("%Y%m%d_%H%M%S")
            filepath = os.path.join(ARCHIVE_OUTPUT_DIR, f"tickets_{timestamp_str}.json.gz")

            with gzip.open(filepath, "wt", encoding="utf-8") as f:
                json.dump(records, f, default=str, ensure_ascii=False)

            # Удаляем чанками
            ids_to_delete = [r["id"] for r in records]
            for i in range(0, len(ids_to_delete), CHUNK_SIZE):
                chunk = ids_to_delete[i : i + CHUNK_SIZE]
                await session.execute(delete(Ticket).where(Ticket.id.in_(chunk)))
                await session.commit()

            return total_archived, filepath

        except Exception:
            await session.rollback()
            logger.warning("Archival: ошибка архивации tickets", exc_info=True)
            return 0, None


async def archive_and_delete_logs(now: datetime) -> tuple[int, str | None]:
    """Архивация и удаление старых логов аудита."""
    cutoff = now - timedelta(days=ARCHIVE_LOG_AGE_DAYS)

    total_archived = 0
    filepath = None

    Path(ARCHIVE_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    async with async_session_maker() as session:
        try:
            stmt = select(Log).where(Log.created_at < cutoff)
            result = await session.execute(stmt)
            logs = list(result.scalars().all())

            if not logs:
                return 0, None

            records = [
                {
                    "id": log_entry.id,
                    "user_id": log_entry.user_id,
                    "action": log_entry.action,
                    "details": log_entry.details,
                    "created_at": log_entry.created_at,
                    "actor_type": log_entry.actor_type,
                    "actor_name": log_entry.actor_name,
                    "http_method": log_entry.http_method,
                    "path": log_entry.path,
                    "status_code": log_entry.status_code,
                    "duration_ms": log_entry.duration_ms,
                    "ip_address": log_entry.ip_address,
                    "is_mutation": log_entry.is_mutation,
                }
                for log_entry in logs
            ]
            total_archived = len(records)

            timestamp_str = now.strftime("%Y%m%d_%H%M%S")
            filepath = os.path.join(ARCHIVE_OUTPUT_DIR, f"logs_{timestamp_str}.json.gz")

            with gzip.open(filepath, "wt", encoding="utf-8") as f:
                json.dump(records, f, default=str, ensure_ascii=False)

            ids_to_delete = [r["id"] for r in records]
            for i in range(0, len(ids_to_delete), CHUNK_SIZE):
                chunk = ids_to_delete[i : i + CHUNK_SIZE]
                await session.execute(delete(Log).where(Log.id.in_(chunk)))
                await session.commit()

            return total_archived, filepath

        except Exception:
            await session.rollback()
            logger.warning("Archival: ошибка архивации logs", exc_info=True)
            return 0, None


async def run_archival(now: datetime | None = None) -> None:
    """Запуск процесса архивации для заявок и логов."""
    current_time = now or datetime.now(UTC)

    tickets_count, tickets_file = await archive_and_delete_tickets(current_time)
    if tickets_count > 0:
        logger.info("Архивировано %d tickets в %s", tickets_count, tickets_file)

    logs_count, logs_file = await archive_and_delete_logs(current_time)
    if logs_count > 0:
        logger.info("Архивировано %d logs в %s", logs_count, logs_file)


async def _archival_loop() -> None:
    """Бесконечный цикл периодической архивации."""
    from core.task_dispatcher import single_instance_guard

    while True:
        try:
            await asyncio.sleep(ARCHIVE_INTERVAL_SECONDS)
            async with single_instance_guard("archival") as is_leader:
                if is_leader:
                    await run_archival()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Archival: непредвиденная ошибка")


def start_archival() -> asyncio.Task:
    """Запускает фоновую архивацию и возвращает asyncio.Task."""
    task = asyncio.create_task(_archival_loop())
    return task
