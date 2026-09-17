"""Тесты outbox-доставки VK-уведомлений (надёжность «ответ в VK после commit»)."""

from sqlalchemy import select

from core.models import Ticket, TicketStatus, User, VkOutbox
from core.outbox import OUTBOX_MAX_ATTEMPTS, add_outbox_message, deliver_pending_messages


async def test_deliver_marks_sent_on_success(db_session_maker, monkeypatch):
    """Успешная отправка помечает сообщение как sent и пишет sent_at."""
    async with db_session_maker() as session:
        entry = add_outbox_message(session, vk_id=111, text="Привет")
        await session.commit()
        message_id = entry.id

    async def _fake_send(vk_id, text):
        assert vk_id == 111
        return True

    monkeypatch.setattr("core.outbox.send_vk_message", _fake_send)

    delivered = await deliver_pending_messages()

    assert delivered == 1
    async with db_session_maker() as session:
        message = await session.get(VkOutbox, message_id)
        assert message.status == "sent"
        assert message.attempts == 1
        assert message.sent_at is not None


async def test_deliver_retries_and_marks_failed(db_session_maker, monkeypatch):
    """При недоступности VK сообщение остаётся pending, после MAX попыток — failed."""
    async with db_session_maker() as session:
        add_outbox_message(session, vk_id=222, text="Не срочно")
        await session.commit()

    async def _fake_send(vk_id, text):
        return False

    monkeypatch.setattr("core.outbox.send_vk_message", _fake_send)

    for _ in range(OUTBOX_MAX_ATTEMPTS - 1):
        await deliver_pending_messages()

    async with db_session_maker() as session:
        message = await session.scalar(select(VkOutbox).where(VkOutbox.vk_id == 222))
        assert message.status == "pending"
        assert message.attempts == OUTBOX_MAX_ATTEMPTS - 1

    # Последняя попытка — окончательная неудача
    await deliver_pending_messages()

    async with db_session_maker() as session:
        message = await session.scalar(select(VkOutbox).where(VkOutbox.vk_id == 222))
        assert message.status == "failed"
        assert message.attempts == OUTBOX_MAX_ATTEMPTS


async def test_reply_to_ticket_writes_outbox_in_same_tx(db_session_maker, monkeypatch):
    """Ответ админа пишет VK-уведомление в outbox (в той же транзакции, не отправляя напрямую)."""
    from core.ticket_service import reply_to_ticket

    async with db_session_maker() as session:
        user = User(vk_id=333)
        session.add(user)
        await session.commit()
        db_user = await session.scalar(select(User).where(User.vk_id == 333))
        ticket = Ticket(user_id=db_user.id, topic="Тест", description="Описание")
        session.add(ticket)
        await session.commit()
        ticket_id = ticket.id

    # Не даём fire_outbox_delivery запускать реальную отправку в тестах
    monkeypatch.setattr("core.ticket_service.fire_outbox_delivery", lambda: None)

    ticket, scheduled = await reply_to_ticket(
        ticket_id=ticket_id,
        admin_username="admin",
        message="Держим в курсе",
    )

    assert ticket is not None
    assert scheduled is True

    async with db_session_maker() as session:
        message = await session.scalar(select(VkOutbox).where(VkOutbox.vk_id == 333))
        assert message is not None
        assert message.status == "pending"
        assert "Держим в курсе" in message.text


async def test_reply_anonymous_ticket_no_outbox(db_session_maker, monkeypatch):
    """Для анонимной заявки VK-уведомление не создаётся."""
    from core.ticket_service import reply_to_ticket

    monkeypatch.setattr("core.ticket_service.fire_outbox_delivery", lambda: None)

    async with db_session_maker() as session:
        ticket = Ticket(
            topic="Аноним",
            is_anonymous=True,
            status=TicketStatus.ANONYMOUS,
        )
        session.add(ticket)
        await session.commit()
        ticket_id = ticket.id

    ticket, scheduled = await reply_to_ticket(
        ticket_id=ticket_id,
        admin_username="admin",
        message="Ответ анониму",
    )

    assert ticket is not None
    assert scheduled is False

    async with db_session_maker() as session:
        # У анонимных user_id в заявке может быть NULL — outbox вообще не должен появиться
        all_outbox = (await session.scalars(select(VkOutbox))).all()
        assert len(all_outbox) == 0


async def test_outbox_temporary_vk_failure_and_recovery(db_session_maker, monkeypatch):
    """Сценарий временного сбоя VK API: сообщения накапливаются и доставляются после восстановления."""
    async with db_session_maker() as session:
        add_outbox_message(session, vk_id=444, text="Сообщение при сбое")
        await session.commit()

    # 1. VK API временно недоступен (сбой сети / 5xx)
    async def _fail_send(vk_id, text):
        return False

    monkeypatch.setattr("core.outbox.send_vk_message", _fail_send)
    delivered = await deliver_pending_messages()
    assert delivered == 0

    async with db_session_maker() as session:
        msg = await session.scalar(select(VkOutbox).where(VkOutbox.vk_id == 444))
        assert msg.status == "pending"
        assert msg.attempts == 1
        assert msg.error is not None

    # 2. VK API восстановился
    async def _success_send(vk_id, text):
        return True

    monkeypatch.setattr("core.outbox.send_vk_message", _success_send)
    delivered = await deliver_pending_messages()
    assert delivered == 1

    async with db_session_maker() as session:
        msg = await session.scalar(select(VkOutbox).where(VkOutbox.vk_id == 444))
        assert msg.status == "sent"
        assert msg.attempts == 2
        assert msg.sent_at is not None
        assert msg.error is None

