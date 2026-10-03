"""Тесты модуля архивации (core/archival.py).

Проверяет:
- Экспорт и удаление старых заявок (gzip JSON)
- Экспорт и удаление старых логов
- Полный цикл run_archival
- Пропуск при отсутствии данных для архивации
"""

import gzip
import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from core.archival import (
    archive_and_delete_logs,
    archive_and_delete_tickets,
    run_archival,
)
from core.models import Log, Ticket, TicketStatus


@pytest.fixture
async def _seed_old_tickets(db_session_maker):
    """Создать старые закрытые заявки и свежую открытую для теста."""
    async with db_session_maker() as session:
        old_time = datetime.now(UTC) - timedelta(days=120)
        session.add(
            Ticket(
                id=9001,
                topic="Old ticket",
                description="Old closed ticket",
                status=TicketStatus.COMPLETED,
                is_anonymous=False,
                auto_closed=False,
                created_at=old_time,
                updated_at=old_time,
            )
        )
        session.add(
            Ticket(
                id=9002,
                topic="New ticket",
                description="Recent open ticket",
                status=TicketStatus.NEW,
                is_anonymous=False,
                auto_closed=False,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()


@pytest.fixture
async def _seed_old_logs(db_session_maker):
    """Создать старые записи аудита."""
    async with db_session_maker() as session:
        old_time = datetime.now(UTC) - timedelta(days=120)
        session.add(
            Log(
                id=9001,
                action="test_old_action",
                created_at=old_time,
                is_mutation=False,
            )
        )
        session.add(
            Log(
                id=9002,
                action="test_new_action",
                created_at=datetime.now(UTC),
                is_mutation=False,
            )
        )
        await session.commit()


@pytest.mark.usefixtures("_seed_old_tickets")
async def test_archive_tickets(db_session_maker, tmp_path, monkeypatch):
    """Старые закрытые заявки архивируются в gzip и удаляются из БД."""
    monkeypatch.setattr("core.archival.ARCHIVE_OUTPUT_DIR", str(tmp_path))
    now = datetime.now(UTC)

    count, filepath = await archive_and_delete_tickets(now)

    assert count >= 1
    assert filepath is not None
    assert filepath.endswith(".json.gz")

    # Проверяем содержимое архива
    with gzip.open(filepath, "rt", encoding="utf-8") as f:
        records = json.load(f)
    assert any(r["id"] == 9001 for r in records)

    # Проверяем что старая заявка удалена из БД
    async with db_session_maker() as session:
        result = await session.execute(text("SELECT id FROM tickets WHERE id = :id"), {"id": 9001})
        assert result.first() is None

        # Свежая заявка осталась
        result = await session.execute(text("SELECT id FROM tickets WHERE id = :id"), {"id": 9002})
        assert result.first() is not None


@pytest.mark.usefixtures("_seed_old_logs")
async def test_archive_logs(db_session_maker, tmp_path, monkeypatch):
    """Старые логи архивируются в gzip и удаляются из БД."""
    monkeypatch.setattr("core.archival.ARCHIVE_OUTPUT_DIR", str(tmp_path))
    now = datetime.now(UTC)

    count, filepath = await archive_and_delete_logs(now)

    assert count >= 1
    assert filepath is not None
    assert filepath.endswith(".json.gz")

    with gzip.open(filepath, "rt", encoding="utf-8") as f:
        records = json.load(f)
    assert any(r["id"] == 9001 for r in records)

    async with db_session_maker() as session:
        result = await session.execute(text("SELECT id FROM logs WHERE id = :id"), {"id": 9001})
        assert result.first() is None

        result = await session.execute(text("SELECT id FROM logs WHERE id = :id"), {"id": 9002})
        assert result.first() is not None


async def test_archive_no_data(db_session_maker, tmp_path, monkeypatch):
    """При отсутствии старых записей архив не создаётся."""
    monkeypatch.setattr("core.archival.ARCHIVE_OUTPUT_DIR", str(tmp_path))
    now = datetime.now(UTC)

    count, filepath = await archive_and_delete_tickets(now)
    assert count == 0
    assert filepath is None

    count, filepath = await archive_and_delete_logs(now)
    assert count == 0
    assert filepath is None


@pytest.mark.usefixtures("_seed_old_tickets", "_seed_old_logs")
async def test_run_archival(db_session_maker, tmp_path, monkeypatch):
    """run_archival успешно архивирует и логи и заявки."""
    monkeypatch.setattr("core.archival.ARCHIVE_OUTPUT_DIR", str(tmp_path))

    await run_archival()

    # Должны появиться файлы архивов
    files = os.listdir(tmp_path)
    ticket_files = [f for f in files if f.startswith("tickets_")]
    log_files = [f for f in files if f.startswith("logs_")]
    assert len(ticket_files) >= 1
    assert len(log_files) >= 1
