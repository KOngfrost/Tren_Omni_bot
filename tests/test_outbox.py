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


async def test_outbox_stable_deterministic_random_id(db_session_maker, monkeypatch):
    """Повторные попытки отправки одного и того же outbox-сообщения используют один и тот же random_id."""
    async with db_session_maker() as session:
        entry = add_outbox_message(session, vk_id=555, text="Тест random_id")
        await session.commit()
        msg_id = entry.id

    captured_random_ids: list[int | None] = []

    async def _mock_send(vk_id, text, random_id=None):
        captured_random_ids.append(random_id)
        # Первая попытка терпит неудачу, вторая успешна
        return len(captured_random_ids) > 1

    monkeypatch.setattr("core.outbox.send_vk_message", _mock_send)

    # Первая попытка
    delivered1 = await deliver_pending_messages()
    assert delivered1 == 0
    assert len(captured_random_ids) == 1
    assert captured_random_ids[0] is not None
    assert captured_random_ids[0] > 0

    # Вторая попытка
    delivered2 = await deliver_pending_messages()
    assert delivered2 == 1
    assert len(captured_random_ids) == 2
    # random_id должен быть СТРОГО идентичен
    assert captured_random_ids[0] == captured_random_ids[1]

    async with db_session_maker() as session:
        msg = await session.get(VkOutbox, msg_id)
        assert msg is not None
        assert msg.status == "sent"
        assert msg.random_id == captured_random_ids[0]


async def test_outbox_lease_timeout_increments_attempts(db_session_maker, monkeypatch):
    """Истечение таймаута захвата (lease timeout) увеличивает attempts и возвращает в pending."""
    from datetime import UTC, datetime, timedelta

    from core.outbox import OUTBOX_CLAIM_TIMEOUT_SECONDS

    stale_time = datetime.now(UTC) - timedelta(seconds=OUTBOX_CLAIM_TIMEOUT_SECONDS + 60)

    async with db_session_maker() as session:
        entry = VkOutbox(
            vk_id=666,
            text="Зависший воркер",
            status="sending",
            attempts=1,
            claimed_at=stale_time,
        )
        session.add(entry)
        await session.commit()
        msg_id = entry.id

    async def _fake_send(vk_id, text, random_id=None):
        return True

    monkeypatch.setattr("core.outbox.send_vk_message", _fake_send)

    # deliver_pending_messages должен сбросить stale -> pending с attempts=2 и затем доставить
    delivered = await deliver_pending_messages()
    assert delivered == 1

    async with db_session_maker() as session:
        msg = await session.get(VkOutbox, msg_id)
        assert msg is not None
        assert msg.status == "sent"
        # attempts: 1 (исходная) + 1 (lease timeout) + 1 (успешная отправка) = 3
        assert msg.attempts == 3


async def test_outbox_lease_timeout_dead_letter(db_session_maker, monkeypatch):
    """Если при lease timeout исчерпан лимит попыток, запись переходит в failed (Dead Letter)."""
    from datetime import UTC, datetime, timedelta

    from core.outbox import OUTBOX_CLAIM_TIMEOUT_SECONDS

    stale_time = datetime.now(UTC) - timedelta(seconds=OUTBOX_CLAIM_TIMEOUT_SECONDS + 60)

    async with db_session_maker() as session:
        entry = VkOutbox(
            vk_id=777,
            text="Poison pill",
            status="sending",
            attempts=OUTBOX_MAX_ATTEMPTS - 1,
            claimed_at=stale_time,
        )
        session.add(entry)
        await session.commit()
        msg_id = entry.id

    await deliver_pending_messages()

    async with db_session_maker() as session:
        msg = await session.get(VkOutbox, msg_id)
        assert msg is not None
        assert msg.status == "failed"
        assert msg.attempts == OUTBOX_MAX_ATTEMPTS
        assert "lease timeout" in (msg.error or "").lower()


async def test_outbox_exponential_backoff(db_session_maker, monkeypatch):
    """При включённом backoff сообщение после неудачи откладывается на next_retry_at."""
    from datetime import UTC, datetime

    from core.config import settings

    monkeypatch.setattr(settings, "OUTBOX_BACKOFF_BASE_SECONDS", 10)

    async with db_session_maker() as session:
        entry = add_outbox_message(session, vk_id=888, text="Backoff тест")
        await session.commit()
        msg_id = entry.id

    send_count = 0

    async def _fail_send(vk_id, text, random_id=None):
        nonlocal send_count
        send_count += 1
        return False

    monkeypatch.setattr("core.outbox.send_vk_message", _fail_send)

    # Первая попытка — терпит неудачу
    await deliver_pending_messages()
    assert send_count == 1

    async with db_session_maker() as session:
        msg = await session.get(VkOutbox, msg_id)
        assert msg is not None
        assert msg.status == "pending"
        assert msg.attempts == 1
        assert msg.next_retry_at is not None
        next_retry = msg.next_retry_at
        if next_retry.tzinfo is None:
            next_retry = next_retry.replace(tzinfo=UTC)
        assert next_retry > datetime.now(UTC)

    # Немедленный второй запуск — должен пропустить сообщение из-за backoff (next_retry_at в будущем)
    await deliver_pending_messages()
    assert send_count == 1  # Отправка не вызывалась повторно!
