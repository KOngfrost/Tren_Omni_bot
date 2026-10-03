"""Тесты для сервиса удаления и анонимизации персональных данных (152-ФЗ / GDPR)."""

import pytest
from sqlalchemy import select

from core.models import (
    Admin,
    Department,
    Subscription,
    Ticket,
    TicketMessage,
    User,
    UserRole,
    VkOutbox,
)
from core.services.privacy_service import AdminAccountDeletionError, delete_user_personal_data


@pytest.mark.asyncio
async def test_delete_user_personal_data_not_found(db_session_maker):
    async with db_session_maker() as session:
        result = await delete_user_personal_data(session, vk_id=999999999)
        assert result["success"] is False
        assert result["error"] == "not_found"


@pytest.mark.asyncio
async def test_delete_user_personal_data_anonymizes_tickets_and_deletes_records(db_session_maker):
    async with db_session_maker() as session:
        # Создаём отдел
        dept = Department(name="Тестовый отдел приватности")
        session.add(dept)
        await session.flush()

        # Создаём студента
        user = User(vk_id=123454321, full_name="Иван Петров", dormitory="Общежитие 1")
        session.add(user)
        await session.flush()

        # Создаём тикет
        ticket = Ticket(
            user_id=user.id,
            department_id=dept.id,
            topic="Вопрос о стипендии",
            description="Личные данные в описании",
            is_anonymous=False,
        )
        session.add(ticket)
        await session.flush()

        # Создаём сообщение
        msg = TicketMessage(
            ticket_id=ticket.id,
            author_type="USER",
            author_vk_id=123454321,
            message="Здравствуйте, проверьте мои данные",
        )
        session.add(msg)

        # Создаём подписку
        sub = Subscription(user_id=user.id, department_id=dept.id)
        session.add(sub)

        # Создаём сообщение в Outbox
        outbox = VkOutbox(vk_id=123454321, text="Ваша заявка принята")
        session.add(outbox)
        await session.commit()

    # Выполняем удаление данных
    async with db_session_maker() as session:
        result = await delete_user_personal_data(session, vk_id=123454321)
        assert result["success"] is True
        assert result["tickets_anonymized"] == 1
        assert result["messages_anonymized"] == 1
        assert result["subscriptions_deleted"] == 1
        assert result["outbox_deleted"] == 1

    # Проверяем состояние БД
    async with db_session_maker() as session:
        # Пользователь удалён
        check_user = await session.scalar(select(User).where(User.vk_id == 123454321))
        assert check_user is None

        # Тикет остался, но анонимизирован
        check_ticket = await session.scalar(select(Ticket).where(Ticket.id == ticket.id))
        assert check_ticket is not None
        assert check_ticket.user_id is None
        assert check_ticket.is_anonymous is True

        # Сообщение осталось, но author_vk_id затёрт
        check_msg = await session.scalar(
            select(TicketMessage).where(TicketMessage.ticket_id == ticket.id)
        )
        assert check_msg is not None
        assert check_msg.author_vk_id is None

        # Подписка удалена
        check_sub = await session.scalar(
            select(Subscription).where(Subscription.department_id == dept.id)
        )
        assert check_sub is None

        # Outbox удалён
        check_outbox = await session.scalar(select(VkOutbox).where(VkOutbox.vk_id == 123454321))
        assert check_outbox is None


@pytest.mark.asyncio
async def test_cannot_delete_active_admin_account_via_student_privacy(db_session_maker):
    async with db_session_maker() as session:
        user = User(vk_id=777777777, full_name="Суперадмин", dormitory="Главный корпус")
        session.add(user)
        await session.flush()

        admin = Admin(user_id=user.id, role=UserRole.SUPERADMIN)
        session.add(admin)
        await session.commit()

    async with db_session_maker() as session:
        with pytest.raises(AdminAccountDeletionError) as exc_info:
            await delete_user_personal_data(session, vk_id=777777777)
        assert "является администратором" in str(exc_info.value)


@pytest.mark.asyncio
async def test_v1_delete_user_data_endpoint(db_session_maker, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from web.routes.api_v1 import v1_delete_user_data

    async with db_session_maker() as session:
        user = User(vk_id=88888888, full_name="Тест API", dormitory="Общ 2")
        session.add(user)
        await session.commit()

    monkeypatch.setattr("web.routes.api_v1.async_session_maker", db_session_maker)
    monkeypatch.setattr("web.routes.api_v1.require_crud_rate_limit", AsyncMock())
    admin_user = {"username": "superadmin", "role": "SUPERADMIN"}
    mock_request = MagicMock()
    resp = await v1_delete_user_data(vk_id=88888888, request=mock_request, user=admin_user)
    assert resp.success is True
    assert resp.vk_id == 88888888
