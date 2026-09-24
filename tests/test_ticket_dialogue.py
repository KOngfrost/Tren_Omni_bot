"""Тесты для истории диалогов в веб-панели и решения заявок администратором в VK."""

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from bots.vk.common import AdminTicketStates
from bots.vk.handlers.admin import (
    admin_quick_complete_handler,
    admin_quick_in_progress_handler,
    admin_reply_start_handler,
    admin_reply_text_handler,
    admin_ticket_history_handler,
    admin_ticket_view_handler,
)
from bots.vk.keyboards import (
    build_admin_reply_cancel_keyboard,
    build_admin_ticket_actions_keyboard,
    build_admin_tickets_list_keyboard,
)
from core.commands import (
    ADMIN_COMPLETE_PATTERN,
    ADMIN_HISTORY_PATTERN,
    ADMIN_IN_PROGRESS_PATTERN,
    ADMIN_REPLY_START_PATTERN,
    ADMIN_TICKET_VIEW_PATTERN,
)
from core.models import Department, MessageAuthorType, Ticket, TicketMessage, TicketStatus, User
from web.routes.tickets import get_ticket, reply_ticket


def test_admin_commands_regex():
    """Проверка regex-шаблонов команд быстрого управления заявками в VK."""
    assert re.match(ADMIN_TICKET_VIEW_PATTERN, "Заявка #42").group(1) == "42"
    assert re.match(ADMIN_TICKET_VIEW_PATTERN, "Заявка 42").group(1) == "42"
    assert re.match(ADMIN_TICKET_VIEW_PATTERN, "заявка #105").group(1) == "105"

    assert re.match(ADMIN_REPLY_START_PATTERN, "Ответить #42").group(1) == "42"
    assert re.match(ADMIN_REPLY_START_PATTERN, "ответить 42").group(1) == "42"

    assert re.match(ADMIN_HISTORY_PATTERN, "История #42").group(1) == "42"
    assert re.match(ADMIN_HISTORY_PATTERN, "Вся история #42").group(1) == "42"

    assert re.match(ADMIN_COMPLETE_PATTERN, "Выполнено #42").group(1) == "42"
    assert re.match(ADMIN_COMPLETE_PATTERN, "выполнено 42").group(1) == "42"

    assert re.match(ADMIN_IN_PROGRESS_PATTERN, "В обработку #42").group(1) == "42"
    assert re.match(ADMIN_IN_PROGRESS_PATTERN, "в обработку 42").group(1) == "42"


def test_admin_keyboards_structure():
    """Проверка генерации клавиатур для администратора в VK."""
    kb_list = build_admin_tickets_list_keyboard([10, 20, 30])
    assert "Заявка #10" in kb_list
    assert "Заявка #20" in kb_list
    assert "Заявки администратора" in kb_list

    kb_actions_new = build_admin_ticket_actions_keyboard(42, is_completed=False)
    assert "Ответить #42" in kb_actions_new
    assert "История #42" in kb_actions_new
    assert "Выполнено #42" in kb_actions_new
    assert "В обработку #42" in kb_actions_new

    kb_actions_completed = build_admin_ticket_actions_keyboard(42, is_completed=True)
    assert "Ответить #42" in kb_actions_completed
    assert "В обработку #42" in kb_actions_completed
    # Кнопка 'Выполнено' скрыта, если заявка уже завершена
    assert "Выполнено #42" not in kb_actions_completed

    kb_cancel = build_admin_reply_cancel_keyboard(42)
    assert "Отмена" in kb_cancel
    assert "Заявка #42" in kb_cancel


@pytest.mark.asyncio
async def test_admin_ticket_view_handler():
    """Проверка отображения карточки заявки администратором."""
    msg = MagicMock()
    msg.from_id = 999999
    msg.text = "Заявка #15"
    msg.answer = AsyncMock()

    mock_ticket = Ticket(
        id=15,
        topic="Шумят соседи",
        description="Очень громкая музыка ночью",
        status=TicketStatus.NEW,
        is_anonymous=False,
        user=User(id=1, vk_id=111, full_name="Иван Петров", dormitory="№3"),
        department=Department(id=2, name="Жил-быт"),
    )

    with (
        patch("bots.vk.handlers.admin._operator_can_access", AsyncMock(return_value=True)),
        patch("bots.vk.handlers.admin.get_ticket_messages", AsyncMock(return_value=[])),
    ):
        mock_session = AsyncMock()
        mock_session.scalar.return_value = mock_ticket
        mock_ctx = MagicMock()
        mock_ctx.__aenter__.return_value = mock_session
        mock_ctx.__aexit__.return_value = None

        with patch("bots.vk.handlers.admin.async_session_maker", return_value=mock_ctx):
            await admin_ticket_view_handler(msg)

    assert msg.answer.called
    reply_text = msg.answer.call_args[0][0]
    assert "Заявка #15" in reply_text
    assert "Иван Петров" in reply_text
    assert "Шумят соседи" in reply_text
    assert "Жил-быт" in reply_text


@pytest.mark.asyncio
async def test_admin_reply_start_and_text_handlers():
    """Проверка входа в FSM ответа и отправки текста ответа."""
    # 1. Начало ответа
    msg_start = MagicMock()
    msg_start.from_id = 999999
    msg_start.text = "Ответить #15"
    msg_start.answer = AsyncMock()

    mock_dispenser = MagicMock()
    mock_dispenser.set = AsyncMock()
    mock_dispenser.get = AsyncMock(return_value=MagicMock(payload={"ticket_id": 15}))
    mock_dispenser.delete = AsyncMock()

    with (
        patch("bots.vk.handlers.admin._operator_can_access", AsyncMock(return_value=True)),
        patch("bots.vk.bot.vk_bot.state_dispenser", mock_dispenser),
    ):
        await admin_reply_start_handler(msg_start)

    assert mock_dispenser.set.called
    assert msg_start.answer.called
    assert "Ответ на заявку #15" in msg_start.answer.call_args[0][0]

    # 2. Отправка текста ответа
    msg_reply = MagicMock()
    msg_reply.from_id = 999999
    msg_reply.text = "Принято в работу, администратор вызван."
    msg_reply.answer = AsyncMock()

    mock_user = MagicMock(full_name="Админ Алексей")
    mock_ticket_after = Ticket(id=15, status=TicketStatus.IN_PROGRESS)

    with (
        patch("bots.vk.bot.vk_bot.state_dispenser", mock_dispenser),
        patch("bots.vk.handlers.admin.BotCore.get_or_create_user", AsyncMock(return_value=mock_user)),
        patch(
            "bots.vk.handlers.admin.reply_to_ticket",
            AsyncMock(return_value=(mock_ticket_after, True)),
        ),
    ):
        await admin_reply_text_handler(msg_reply)

    assert mock_dispenser.delete.called
    assert msg_reply.answer.called
    assert "Ответ успешно сохранён в заявке #15" in msg_reply.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_admin_quick_status_handlers():
    """Проверка кнопок 'Выполнено #N' и 'В обработку #N'."""
    msg_done = MagicMock()
    msg_done.from_id = 999999
    msg_done.text = "Выполнено #15"
    msg_done.answer = AsyncMock()

    mock_ticket_done = Ticket(id=15, status=TicketStatus.COMPLETED)
    with (
        patch("bots.vk.handlers.admin._operator_can_access", AsyncMock(return_value=True)),
        patch(
            "bots.vk.handlers.admin.change_ticket_status",
            AsyncMock(return_value=mock_ticket_done),
        ),
    ):
        await admin_quick_complete_handler(msg_done)

    assert msg_done.answer.called
    assert "переведена в статус «Выполнено»" in msg_done.answer.call_args[0][0]

    msg_prog = MagicMock()
    msg_prog.from_id = 999999
    msg_prog.text = "В обработку #15"
    msg_prog.answer = AsyncMock()

    mock_ticket_prog = Ticket(id=15, status=TicketStatus.IN_PROGRESS)
    with (
        patch("bots.vk.handlers.admin._operator_can_access", AsyncMock(return_value=True)),
        patch(
            "bots.vk.handlers.admin.change_ticket_status",
            AsyncMock(return_value=mock_ticket_prog),
        ),
    ):
        await admin_quick_in_progress_handler(msg_prog)

    assert msg_prog.answer.called
    assert "переведена в статус «В обработке»" in msg_prog.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_web_ticket_api_ajax_reply():
    """Проверка AJAX-ответа на заявку через веб-панель."""
    mock_ticket = Ticket(
        id=77,
        topic="Вопрос по общежитию",
        description="Не греет батарея",
        status=TicketStatus.IN_PROGRESS,
        is_anonymous=False,
    )

    req = MagicMock(spec=Request)
    req.headers = {
        "content-type": "application/json",
        "accept": "application/json",
        "x-requested-with": "XMLHttpRequest",
    }
    req.json = AsyncMock(
        return_value={"message": "Сантехник прибудет в 14:00", "complete": False}
    )

    with (
        patch("web.routes.tickets.require_crud_rate_limit", AsyncMock()),
        patch("web.routes.tickets._load_ticket_for_user", AsyncMock(return_value=mock_ticket)),
        patch(
            "web.routes.tickets.reply_to_ticket",
            AsyncMock(return_value=(mock_ticket, True)),
        ),
    ):
        resp = await reply_ticket(
            ticket_id=77,
            request=req,
            user={"username": "superadmin", "role": "SUPERADMIN"},
        )

    assert resp["success"] is True
    assert resp["ticket_id"] == 77
    assert resp["vk_sent"] is True
    assert "Сантехник прибудет в 14:00" in resp["message"]["message"]
    assert "МСК" in resp["message"]["created_at_display"]
