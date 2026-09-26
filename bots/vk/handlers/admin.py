"""Хендлеры администраторов: админ-панель, просмотр заявок, интерактивное решение вопросов и смена статуса."""

import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from vkbottle.bot import BotLabeler, Message
from vkbottle.dispatch.rules.base import RegexRule

from bots.vk.common import (
    AdminTicketStates,
    _get_department_names,
    _main_keyboard_for,
    _main_reply_text,
    _operator_can_access,
)
from bots.vk.handlers.pagination import (
    FETCH_LIMIT,
    KIND_ADMIN_TICKETS,
    PAGE_SIZE,
    clamp_page,
    open_list,
    page_count,
    persist_page,
    register_renderer,
)
from bots.vk.keyboards import (
    build_admin_keyboard,
    build_admin_reply_cancel_keyboard,
    build_admin_ticket_actions_keyboard,
    build_admin_tickets_list_keyboard,
    build_main_keyboard,
)
from core.bot_core import BotCore, get_admin_scope_for_vk_id
from core.commands import (
    ADMIN_COMPLETE_PATTERN,
    ADMIN_HISTORY_PATTERN,
    ADMIN_IN_PROGRESS_PATTERN,
    ADMIN_REPLY_START_PATTERN,
    ADMIN_STATUS_PATTERN,
    ADMIN_TICKET_VIEW_PATTERN,
    COMMANDS_ADMIN,
    COMMANDS_ADMIN_TICKETS,
    COMMANDS_REGULAR_MENU,
)
from core.database import async_session_maker
from core.heartbeat import touch_heartbeat
from core.models import MessageAuthorType, Ticket, TicketStatus
from core.reporting import get_app_tz
from core.ticket_service import (
    COMPLETED_STATUSES,
    StatusTransitionError,
    change_ticket_status,
    get_ticket_messages,
    mask_anonymous_data,
    reply_to_ticket,
    status_label,
)

logger = logging.getLogger(__name__)

admin_labeler = BotLabeler()


@admin_labeler.private_message(text=COMMANDS_ADMIN)
async def admin_panel(message: Message):
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    if not await BotCore.is_admin(user):
        await message.answer(
            "У вас нет доступа к админ-панели.\n\n"
            "По всем вопросам обращайтесь к Техническому администратору.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return

    await BotCore.log_action(user, "admin_panel", "Открыта админ-панель")
    await message.answer(
        "Админ-панель\n\n"
        "Доступные функции:\n"
        "• Просмотр и решение заявок студентов за сегодняшний день\n"
        "• Формирование оперативных отчётов\n\n"
        "Нажмите «Заявки администратора» для работы с обращениями.",
        keyboard=build_admin_keyboard(),
    )


@admin_labeler.private_message(text=COMMANDS_REGULAR_MENU)
async def regular_menu_handler(message: Message):
    touch_heartbeat()
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    departments = await _get_department_names()
    keyboard = build_main_keyboard(await BotCore.is_admin(user), departments)
    await message.answer(
        _main_reply_text(departments),
        keyboard=keyboard,
    )


async def _render_admin_tickets_page(message: Message, page: int, meta: dict[str, Any]) -> None:
    """Отрисовать страницу списка активных заявок администратора за сегодняшний день (пагинация)."""
    async with async_session_maker() as session:
        is_super, dept_id = await get_admin_scope_for_vk_id(session, message.from_id)

    if not is_super and dept_id is None:
        await message.answer(
            "У вас нет доступа к заявкам администратора.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return

    now_msk = datetime.now(get_app_tz())
    today_start = datetime(now_msk.year, now_msk.month, now_msk.day, 0, 0, 0, tzinfo=get_app_tz())
    today_str = now_msk.strftime("%d.%m.%Y")

    async with async_session_maker() as session:
        stmt = (
            select(Ticket)
            .options(selectinload(Ticket.department), selectinload(Ticket.user))
            .where(Ticket.created_at >= today_start)
            .order_by(Ticket.created_at.desc())
            .limit(FETCH_LIMIT)
        )
        if not is_super and dept_id is not None:
            stmt = stmt.where(Ticket.department_id == dept_id)

        count_stmt = select(func.count(Ticket.id)).where(
            Ticket.created_at >= today_start,
            Ticket.status.not_in((TicketStatus.COMPLETED, TicketStatus.COMPLETED_AUTO)),
        )
        total_count_stmt = select(func.count(Ticket.id)).where(Ticket.created_at >= today_start)
        if not is_super and dept_id is not None:
            count_stmt = count_stmt.where(Ticket.department_id == dept_id)
            total_count_stmt = total_count_stmt.where(Ticket.department_id == dept_id)

        pending_count = await session.scalar(count_stmt)
        total_count = await session.scalar(total_count_stmt)
        tickets = list((await session.scalars(stmt)).all())

    page = clamp_page(page, len(tickets))
    await persist_page(message.from_id, KIND_ADMIN_TICKETS, page, meta)

    if not tickets:
        await message.answer(
            f"Заявок за сегодня ({today_str}) пока нет.\n\n"
            f"Всего за сегодня: {total_count or 0}\n"
            f"В обработке / новых: {pending_count or 0}",
            keyboard=build_admin_keyboard(),
        )
        return

    total_pages = page_count(len(tickets))
    start = page * PAGE_SIZE
    page_tickets = tickets[start : start + PAGE_SIZE]

    ticket_ids = [t.id for t in page_tickets if t.id is not None]
    lines = [
        f"📊 Всего заявок: {total_count or 0} · Требуют внимания: {pending_count or 0}",
        "Последние заявки:",
        "",
    ]
    for ticket in page_tickets:
        dept_name = ticket.department.name if ticket.department else "Без отдела"
        desc = (ticket.description or "Без описания").strip()
        if len(desc) > 80:
            desc = desc[:80].rstrip() + "..."
        lines.append(f"#{ticket.id} [{status_label(ticket.status)}] · {dept_name}")
        lines.append(f"   Тема: {ticket.topic or 'Без темы'}")
        lines.append(f"   Текст: {desc}")
        lines.append("")

    if total_pages > 1:
        lines.append(f"📄 Страница {page + 1} из {total_pages}")
    lines.append("Нажмите кнопку с номером заявки ниже, чтобы открыть её и ответить:")
    await message.answer(
        "\n".join(lines),
        keyboard=build_admin_tickets_list_keyboard(
            ticket_ids, page=page, has_more=page + 1 < total_pages
        ),
    )


@admin_labeler.private_message(text=COMMANDS_ADMIN_TICKETS)
async def admin_tickets_handler(message: Message):
    """Показать оператору список активных заявок с кнопками быстрого открытия."""
    touch_heartbeat()
    await open_list(message, KIND_ADMIN_TICKETS)


@admin_labeler.private_message(RegexRule(ADMIN_TICKET_VIEW_PATTERN))
async def admin_ticket_view_handler(message: Message):
    """Просмотр подробной карточки заявки администратором с кнопками действий."""
    touch_heartbeat()
    match = re.match(ADMIN_TICKET_VIEW_PATTERN, message.text or "", re.DOTALL)
    if not match:
        return
    ticket_id = int(match.group(1))

    if not await _operator_can_access(message.from_id, ticket_id):
        user = await BotCore.get_or_create_user(vk_id=message.from_id)
        if not await BotCore.is_admin(user):
            return
        await message.answer(
            f"Заявка #{ticket_id} не найдена или относится к другому отделу.",
            keyboard=build_admin_keyboard(),
        )
        return

    async with async_session_maker() as session:
        ticket = await session.scalar(
            select(Ticket)
            .options(selectinload(Ticket.department), selectinload(Ticket.user))
            .where(Ticket.id == ticket_id)
        )

    if ticket is None:
        await message.answer("Заявка не найдена.", keyboard=build_admin_keyboard())
        return

    messages = await get_ticket_messages(ticket_id)
    dept_name = ticket.department.name if ticket.department else "Без отдела"
    student_name = (
        "Аноним"
        if ticket.is_anonymous
        else (ticket.user.full_name if ticket.user and ticket.user.full_name else f"VK ID {ticket.user.vk_id if ticket.user else '—'}")
    )
    dorm = (
        f" (Общежитие: {ticket.user.dormitory})"
        if (ticket.user and ticket.user.dormitory and not ticket.is_anonymous)
        else ""
    )
    created_str = ticket.created_at.strftime("%d.%m.%Y %H:%M") if ticket.created_at else "—"

    lines = [
        f"📋 Заявка #{ticket.id}",
        f"🏢 Отдел: {dept_name}",
        f"👤 Студент: {student_name}{dorm}",
        f"📌 Тема: {ticket.topic or 'Без темы'}",
        f"📝 Описание: {ticket.description or '—'}",
        f"📊 Статус: {status_label(ticket.status)}",
        f"🕒 Создана: {created_str} МСК",
        "",
    ]

    if messages:
        lines.append(f"💬 Последние сообщения ({len(messages)}):")
        for m in messages[-3:]:
            author = {
                MessageAuthorType.USER: "Студент",
                MessageAuthorType.ADMIN: "Администратор",
                MessageAuthorType.SYSTEM: "Система",
            }.get(m.author_type, "—")
            m_time = m.created_at.strftime("%H:%M") if m.created_at else ""
            lines.append(f"• [{m_time}] {author}: {m.message[:120]}{'...' if len(m.message) > 120 else ''}")
    elif ticket.response_text:
        lines.append(f"💬 Ответ: {ticket.response_text}")

    lines.append("")
    lines.append("Выберите действие ниже:")
    is_completed = ticket.status in COMPLETED_STATUSES
    await message.answer(
        "\n".join(lines), keyboard=build_admin_ticket_actions_keyboard(ticket.id, is_completed)
    )


@admin_labeler.private_message(RegexRule(ADMIN_HISTORY_PATTERN))
async def admin_ticket_history_handler(message: Message):
    """Просмотр полной истории переписки по заявке."""
    touch_heartbeat()
    match = re.match(ADMIN_HISTORY_PATTERN, message.text or "", re.DOTALL)
    if not match:
        return
    ticket_id = int(match.group(1))

    if not await _operator_can_access(message.from_id, ticket_id):
        await message.answer("Заявка не найдена или недоступна.", keyboard=build_admin_keyboard())
        return

    async with async_session_maker() as session:
        ticket = await session.scalar(
            select(Ticket)
            .options(selectinload(Ticket.department))
            .where(Ticket.id == ticket_id)
        )

    if ticket is None:
        await message.answer("Заявка не найдена.", keyboard=build_admin_keyboard())
        return

    messages = await get_ticket_messages(ticket_id)
    lines = [
        f"📜 Вся история переписки по заявке #{ticket.id}",
        f"Тема: {ticket.topic or 'Без темы'} · Статус: {status_label(ticket.status)}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    if not messages:
        lines.append(f"Студент: {ticket.description or '—'}")
        if ticket.response_text:
            lines.append(f"Администратор: {ticket.response_text}")
    else:
        for m in messages:
            author = {
                MessageAuthorType.USER: "Студент",
                MessageAuthorType.ADMIN: "Администратор",
                MessageAuthorType.SYSTEM: "Система",
            }.get(m.author_type, "—")
            m_time = m.created_at.strftime("%d.%m %H:%M") if m.created_at else ""
            lines.append(f"[{m_time}] {author}: {m.message}")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    is_completed = ticket.status in COMPLETED_STATUSES
    await message.answer(
        "\n".join(lines), keyboard=build_admin_ticket_actions_keyboard(ticket.id, is_completed)
    )


@admin_labeler.private_message(RegexRule(ADMIN_REPLY_START_PATTERN))
async def admin_reply_start_handler(message: Message):
    """Начало интерактивного ответа на заявку (ввод текста)."""
    touch_heartbeat()
    match = re.match(ADMIN_REPLY_START_PATTERN, message.text or "", re.DOTALL)
    if not match:
        return
    ticket_id = int(match.group(1))

    if not await _operator_can_access(message.from_id, ticket_id):
        await message.answer("Заявка не найдена или недоступна.", keyboard=build_admin_keyboard())
        return

    from bots.vk.bot import vk_bot

    await vk_bot.state_dispenser.set(
        message.from_id,
        AdminTicketStates.WAITING_REPLY_TEXT,
        ticket_id=ticket_id,
    )
    await message.answer(
        f"✍️ Ответ на заявку #{ticket_id}\n\n"
        "Введите текст вашего ответа одним сообщением.\n"
        "Студент получит его в личные сообщения от имени бота.\n\n"
        "Для отмены нажмите «Отмена» ниже.",
        keyboard=build_admin_reply_cancel_keyboard(ticket_id),
    )


@admin_labeler.private_message(state=AdminTicketStates.WAITING_REPLY_TEXT)
async def admin_reply_text_handler(message: Message):
    """Приём введённого администратором текста ответа и отправка студенту."""
    touch_heartbeat()
    from bots.vk.bot import vk_bot

    state_peer = await vk_bot.state_dispenser.get(message.from_id)
    ticket_id = state_peer.payload.get("ticket_id") if state_peer else None

    raw_text = (message.text or "").strip()
    if raw_text.casefold() in ("отмена", "отменить"):
        await vk_bot.state_dispenser.delete(message.from_id)
        keyboard = (
            build_admin_ticket_actions_keyboard(ticket_id)
            if ticket_id
            else build_admin_keyboard()
        )
        await message.answer("Ввод ответа отменён.", keyboard=keyboard)
        return

    if not ticket_id:
        await vk_bot.state_dispenser.delete(message.from_id)
        await message.answer("Сессия ответа истекла. Пожалуйста, откройте заявку заново.", keyboard=build_admin_keyboard())
        return

    if len(raw_text) < 2:
        await message.answer("Текст ответа слишком короткий. Введите ответ подробнее или нажмите «Отмена»:")
        return

    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    admin_name = user.full_name or f"VK:{message.from_id}"

    ticket, delivered = await reply_to_ticket(
        ticket_id=ticket_id,
        admin_username=admin_name,
        message=raw_text,
        complete=False,
    )
    await vk_bot.state_dispenser.delete(message.from_id)

    if ticket is None:
        await message.answer("Заявка не найдена.", keyboard=build_admin_keyboard())
        return

    note = " (уведомление отправлено студенту в VK)" if delivered else " (заявка анонимная или без VK ID)"
    await message.answer(
        f"✅ Ответ успешно сохранён в заявке #{ticket_id}{note}.\n"
        f"Текущий статус: {status_label(ticket.status)}.",
        keyboard=build_admin_ticket_actions_keyboard(ticket_id, is_completed=False),
    )


@admin_labeler.private_message(RegexRule(ADMIN_COMPLETE_PATTERN))
async def admin_quick_complete_handler(message: Message):
    """Быстрое закрытие заявки по кнопке «Выполнено #N»."""
    touch_heartbeat()
    match = re.match(ADMIN_COMPLETE_PATTERN, message.text or "", re.DOTALL)
    if not match:
        return
    ticket_id = int(match.group(1))

    if not await _operator_can_access(message.from_id, ticket_id):
        await message.answer("Заявка не найдена или недоступна.", keyboard=build_admin_keyboard())
        return

    try:
        ticket = await change_ticket_status(
            ticket_id, TicketStatus.COMPLETED, f"VK:{message.from_id}"
        )
    except (ValueError, StatusTransitionError) as e:
        await message.answer(f"Не удалось завершить заявку: {e}", keyboard=build_admin_keyboard())
        return

    if ticket is None:
        await message.answer("Заявка не найдена.", keyboard=build_admin_keyboard())
        return

    await message.answer(
        f"✅ Заявка #{ticket_id} переведена в статус «Выполнено».",
        keyboard=build_admin_ticket_actions_keyboard(ticket_id, is_completed=True),
    )


@admin_labeler.private_message(RegexRule(ADMIN_IN_PROGRESS_PATTERN))
async def admin_quick_in_progress_handler(message: Message):
    """Быстрый перевод заявки в статус «В обработку #N»."""
    touch_heartbeat()
    match = re.match(ADMIN_IN_PROGRESS_PATTERN, message.text or "", re.DOTALL)
    if not match:
        return
    ticket_id = int(match.group(1))

    if not await _operator_can_access(message.from_id, ticket_id):
        await message.answer("Заявка не найдена или недоступна.", keyboard=build_admin_keyboard())
        return

    try:
        ticket = await change_ticket_status(
            ticket_id, TicketStatus.IN_PROGRESS, f"VK:{message.from_id}"
        )
    except (ValueError, StatusTransitionError) as e:
        await message.answer(f"Не удалось изменить статус: {e}", keyboard=build_admin_keyboard())
        return

    if ticket is None:
        await message.answer("Заявка не найдена.", keyboard=build_admin_keyboard())
        return

    await message.answer(
        f"🔄 Заявка #{ticket_id} переведена в статус «В обработке».",
        keyboard=build_admin_ticket_actions_keyboard(ticket_id, is_completed=False),
    )


def _parse_status(value: str) -> TicketStatus:
    """Распознать статус по имени enum или по русской метке (StrEnum.value)."""
    value = value.strip()
    try:
        return TicketStatus(value)
    except ValueError:
        pass
    for status in TicketStatus:
        if status.value.lower() == value.lower():
            return status
    raise ValueError(f"Неизвестный статус: {value!r}")


@admin_labeler.private_message(RegexRule(ADMIN_STATUS_PATTERN))
async def admin_status_handler(message: Message):
    match = re.match(ADMIN_STATUS_PATTERN, message.text or "", re.DOTALL)
    if not match or not await _operator_can_access(message.from_id, int(match.group(1))):
        user = await BotCore.get_or_create_user(vk_id=message.from_id)
        is_adm = await BotCore.is_admin(user)
        keyboard = build_admin_keyboard() if is_adm else await _main_keyboard_for(message.from_id)
        await message.answer(
            "Заявка не найдена или недоступна.",
            keyboard=keyboard,
        )
        return
    try:
        new_status = _parse_status(match.group(2))
        ticket = await change_ticket_status(int(match.group(1)), new_status, str(message.from_id))
    except (ValueError, StatusTransitionError):
        await message.answer(
            "Неизвестный статус или недопустимый переход.\n"
            " Допустимые статусы: "
            " Новое,"
            " В обработке,"
            " Выполнено,"
            " Передано в администрацию,"
            " Передано в хозчасть.",
            keyboard=build_admin_keyboard(),
        )
        return
    await message.answer(
        f"Статус заявки #{match.group(1)} изменён: {status_label(ticket.status)}."
        if ticket
        else "Заявка не найдена.",
        keyboard=build_admin_keyboard(),
    )


# Рендерер пагинации списка заявок администратора (ЭТАП 4.1 / B4)
register_renderer(KIND_ADMIN_TICKETS, _render_admin_tickets_page)

# Алиасы для обратной совместимости
admin_panel_handler = admin_panel
admin_status_change_handler = admin_status_handler
