"""Тесты сервиса массовой рассылки (core.services.broadcast_service)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from core.models import Log, User, VkOutbox
from core.services.broadcast_service import (
    enqueue_broadcast,
    get_recent_users,
    get_total_users_count,
)


@pytest.mark.asyncio
async def test_get_recent_users_ordering(db_session_maker):
    """Проверка выборки N пользователей с упорядочиванием по последней активности."""
    now = datetime.now(UTC)
    async with db_session_maker() as session:
        u1 = User(
            vk_id=101,
            full_name="Иван Иванов",
            created_at=now - timedelta(days=10),
            last_seen_at=now - timedelta(days=5),
        )
        u2 = User(
            vk_id=102,
            full_name="Петр Петров",
            created_at=now - timedelta(days=8),
            last_seen_at=now - timedelta(hours=1),  # Самый активный
        )
        u3 = User(
            vk_id=103,
            full_name="Сергей Сергеев",
            created_at=now - timedelta(days=2),
            last_seen_at=now - timedelta(hours=2),  # Второй по активности
        )
        session.add_all([u1, u2, u3])
        await session.commit()

        # Запрашиваем 2 последних пользователя
        recent = await get_recent_users(session, limit=2)
        assert len(recent) == 2
        assert recent[0].vk_id == 102
        assert recent[1].vk_id == 103

        # Запрашиваем с лимитом <= 0
        empty = await get_recent_users(session, limit=0)
        assert empty == []

        total = await get_total_users_count(session)
        assert total == 3


@pytest.mark.asyncio
async def test_enqueue_broadcast_success(db_session_maker, monkeypatch):
    """Успешная постановка рассылки в outbox и запись в журнал аудита."""
    now = datetime.now(UTC)
    fired = []

    def _mock_fire():
        fired.append(True)

    monkeypatch.setattr("core.services.broadcast_service.fire_outbox_delivery", _mock_fire)

    async with db_session_maker() as session:
        u1 = User(vk_id=201, full_name="Анна", created_at=now - timedelta(days=3))
        u2 = User(vk_id=202, full_name="Мария", created_at=now - timedelta(days=1))
        session.add_all([u1, u2])
        await session.commit()

        result = await enqueue_broadcast(
            session=session,
            text="Собрание студсовета завтра в 18:00!",
            count=2,
            author_name="admin_user",
            actor_type="web",
        )

        assert result.status == "success"
        assert result.recipients_count == 2
        assert set(result.recipient_vk_ids) == {201, 202}
        assert len(fired) == 1

        # Проверяем записи в vk_outbox
        outbox_items = (
            await session.scalars(select(VkOutbox).order_by(VkOutbox.vk_id.asc()))
        ).all()
        assert len(outbox_items) == 2
        assert outbox_items[0].vk_id == 201
        assert outbox_items[0].text == "Собрание студсовета завтра в 18:00!"
        assert outbox_items[0].status == "pending"

        # Проверяем журнал аудита
        log_entry = await session.scalar(select(Log).where(Log.action == "broadcast_sent"))
        assert log_entry is not None
        assert log_entry.actor_name == "admin_user"
        assert log_entry.actor_type == "web"
        assert "2 получателей" in log_entry.details


@pytest.mark.asyncio
async def test_enqueue_broadcast_validation(db_session_maker):
    """Проверка валидации входных параметров рассылки."""
    async with db_session_maker() as session:
        # Пустой текст
        r1 = await enqueue_broadcast(session, text="   ", count=5)
        assert r1.status == "error"
        assert "пустым" in (r1.error or "")

        # Некорректное число N
        r2 = await enqueue_broadcast(session, text="Тест", count=0)
        assert r2.status == "error"
        assert "положительным" in (r2.error or "")

        # Нет пользователей в БД
        r3 = await enqueue_broadcast(session, text="Тест", count=5)
        assert r3.status == "error"
        assert "нет пользователей" in (r3.error or "")
