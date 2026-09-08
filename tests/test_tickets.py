"""Тесты сервиса заявок: переходы статусов, форматирование, маскировка."""

from datetime import datetime

import pytest

from core.models import Department, MessageAuthorType, Ticket, TicketMessage, TicketStatus, User
from core.ticket_service import (
    StatusTransitionError,
    can_transition,
    format_ticket_details,
    format_ticket_list,
    mask_anonymous_data,
    validate_transition,
)


def test_transition_new_to_in_progress_allowed():
    assert can_transition(TicketStatus.NEW, TicketStatus.IN_PROGRESS)


def test_transition_new_to_completed_allowed():
    assert can_transition(TicketStatus.NEW, TicketStatus.COMPLETED)


def test_transition_completed_to_new_forbidden():
    assert not can_transition(TicketStatus.COMPLETED, TicketStatus.NEW)


def test_transition_completed_to_auto_allowed():
    assert can_transition(TicketStatus.COMPLETED, TicketStatus.COMPLETED_AUTO)


def test_transition_auto_can_reopen():
    # При ответе студента статус COMPLETED_AUTO возвращается в IN_PROGRESS
    assert can_transition(TicketStatus.COMPLETED_AUTO, TicketStatus.IN_PROGRESS)
    assert not can_transition(TicketStatus.COMPLETED_AUTO, TicketStatus.NEW)


def test_validate_transition_raises():
    with pytest.raises(StatusTransitionError):
        validate_transition(TicketStatus.COMPLETED, TicketStatus.NEW)


def test_validate_transition_same_status_ok():
    # Тот же статус — не ошибка (повторное сохранение)
    validate_transition(TicketStatus.IN_PROGRESS, TicketStatus.IN_PROGRESS)


def test_format_ticket_list_empty():
    assert "нет заявок" in format_ticket_list([]).lower()


def test_format_ticket_list_contains_ticket_info():
    ticket = Ticket(
        id=12,
        topic="Не работает свет",
        status=TicketStatus.IN_PROGRESS,
        response_text="Уже выехали",
    )
    ticket.department = Department(name="Жилбыт")
    text = format_ticket_list([ticket])
    assert "#12" in text
    assert "Жилбыт" in text
    assert "Не работает свет" in text
    assert "В обработке" in text
    assert "Уже выехали" in text


def test_format_ticket_details_with_history():
    ticket = Ticket(id=7, topic="Тема", description="Описание", status=TicketStatus.NEW)
    ticket.department = Department(name="Информ")
    messages = [
        TicketMessage(
            ticket_id=7,
            author_type=MessageAuthorType.USER,
            message="Вопрос от студента",
        ),
        TicketMessage(
            ticket_id=7,
            author_type=MessageAuthorType.ADMIN,
            message="Ответ администратора",
        ),
    ]
    text = format_ticket_details(ticket, messages)
    assert "Заявка #7" in text
    assert "Информ" in text
    assert "Вопрос от студента" in text
    assert "Ответ администратора" in text


def test_mask_anonymous_data():
    assert mask_anonymous_data("Иван Иванов", True) == "Аноним"
    assert mask_anonymous_data("Иван Иванов", False) == "Иван Иванов"
    assert mask_anonymous_data(None, False) == "—"


async def test_get_user_tickets_only_own(db_session_maker):
    from core.ticket_service import get_user_tickets

    async with db_session_maker() as session:
        user = User(vk_id=111)
        other = User(vk_id=222)
        session.add_all([user, other])
        await session.flush()
        session.add_all(
            [
                Ticket(user_id=user.id, topic="Своя заявка"),
                Ticket(user_id=other.id, topic="Чужая заявка"),
            ]
        )
        await session.commit()

    tickets = await get_user_tickets(111, include_completed=True)
    assert len(tickets) == 1
    assert tickets[0].topic == "Своя заявка"


async def test_get_user_tickets_excludes_completed(db_session_maker):
    from core.ticket_service import get_user_tickets

    async with db_session_maker() as session:
        user = User(vk_id=333)
        session.add(user)
        await session.flush()
        session.add_all(
            [
                Ticket(user_id=user.id, topic="Активная"),
                Ticket(user_id=user.id, topic="Закрытая", status=TicketStatus.COMPLETED),
            ]
        )
        await session.commit()

    tickets = await get_user_tickets(333, include_completed=False)
    assert len(tickets) == 1
    assert tickets[0].topic == "Активная"


async def test_get_user_tickets_pagination(db_session_maker):
    """Пагинация: limit/offset работают и не нарушают порядок (новые сверху)."""
    from sqlalchemy import select

    from core.ticket_service import get_user_tickets

    async with db_session_maker() as session:
        user = User(vk_id=444)
        session.add(user)
        await session.commit()
        db_user = await session.scalar(select(User).where(User.vk_id == 444))

        for i in range(7):
            session.add(
                Ticket(
                    user_id=db_user.id,
                    topic=f"Заявка {i}",
                    created_at=datetime(2026, 9, 1, 10, i),
                )
            )
        await session.commit()

    first_page = await get_user_tickets(444, include_completed=True, limit=3, offset=0)
    second_page = await get_user_tickets(444, include_completed=True, limit=3, offset=3)

    assert len(first_page) == 3
    assert len(second_page) == 3
    # Второй набор не пересекается с первым по теме
    topics1 = {t.topic for t in first_page}
    topics2 = {t.topic for t in second_page}
    assert topics1.isdisjoint(topics2)
    # Новые сверху: на первой странице номера (по created_at) выше
    assert first_page[0].created_at >= first_page[1].created_at
