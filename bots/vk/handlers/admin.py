"""Хендлеры администраторов: админ-панель, просмотр заявок, смена статуса."""

import re

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from vkbottle.bot import BotLabeler, Message
from vkbottle.dispatch.rules.base import RegexRule

from bots.vk.common import (
    _get_department_names,
    _main_keyboard_for,
    _main_reply_text,
    _operator_can_access,
)
from bots.vk.keyboards import build_admin_keyboard, build_main_keyboard
from core.bot_core import BotCore, get_admin_scope_for_vk_id
from core.commands import (
    ADMIN_STATUS_PATTERN,
    COMMANDS_ADMIN,
    COMMANDS_ADMIN_TICKETS,
    COMMANDS_REGULAR_MENU,
)
from core.database import async_session_maker
from core.heartbeat import touch_heartbeat
from core.models import Ticket, TicketStatus
from core.ticket_service import (
    STATUS_LABELS,
    StatusTransitionError,
    change_ticket_status,
    status_label,
)

admin_labeler = BotLabeler()


@admin_labeler.private_message(text=COMMANDS_ADMIN)
async def admin_panel(message: Message):
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    if not await BotCore.is_admin(user):
        await message.answer(
            "У вас нет доступа к админ-панели.\n\n"
            "По всем вопросам обращайтесь к главному администратору.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return

    await BotCore.log_action(user, "admin_panel", "Открыта админ-панель")
    await message.answer(
        "Админ-панель\n\n"
        "Доступные функции: просмотр заявок, формирование отчётов.\n"
        "Нажмите «Заявки администратора» для просмотра активных заявок.\n"
        "Нажмите «Сформировать отчет», чтобы получить файл отчёта в VK.",
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


@admin_labeler.private_message(text=COMMANDS_ADMIN_TICKETS)
async def admin_tickets_handler(message: Message):
    """Показать оператору заявки, доступные его отделу."""
    async with async_session_maker() as session:
        is_super, dept_id = await get_admin_scope_for_vk_id(session, message.from_id)

    if not is_super and dept_id is None:
        await message.answer(
            "У вас нет доступа к заявкам администратора.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return

    async with async_session_maker() as session:
        stmt = (
            select(Ticket)
            .options(selectinload(Ticket.department))
            .order_by(Ticket.created_at.desc())
            .limit(20)
        )
        if not is_super and dept_id is not None:
            stmt = stmt.where(Ticket.department_id == dept_id)
        count_stmt = select(func.count(Ticket.id)).where(
            Ticket.status.not_in((TicketStatus.COMPLETED, TicketStatus.COMPLETED_AUTO))
        )
        total_count_stmt = select(func.count(Ticket.id))
        if not is_super and dept_id is not None:
            count_stmt = count_stmt.where(Ticket.department_id == dept_id)
            total_count_stmt = total_count_stmt.where(Ticket.department_id == dept_id)
        pending_count = await session.scalar(count_stmt)
        total_count = await session.scalar(total_count_stmt)
        tickets = list((await session.scalars(stmt)).all())
    if not tickets:
        await message.answer(
            f"Всего доступных заявок: {total_count or 0}\n"
            f"Незавершенных заявок: {pending_count or 0}\n"
            "Доступных заявок нет.",
            keyboard=build_admin_keyboard(),
        )
        return
    lines = [
        f"Всего доступных заявок: {total_count or 0}",
        f"Незавершенных заявок: {pending_count or 0}",
        "Заявки (последние 20):",
    ]
    for ticket in tickets:
        description = (ticket.description or "Без описания").strip()
        if len(description) > 500:
            description = description[:500].rstrip() + "..."
        lines.append(
            f"\n#{ticket.id} [{status_label(ticket.status)}] {ticket.topic or 'Без темы'}\n"
            f"Описание: {description}"
        )
    lines.append("")
    lines.append("Как ответить на заявку:")
    lines.append("  Ответ #N: текст ответа. Например: «Ответ #12: Уже исправили»")
    lines.append("  Статус #N: статус. Например: «Статус #12: Выполнено»")
    await message.answer("\n".join(lines), keyboard=build_admin_keyboard())


def _parse_status(value: str) -> TicketStatus:
    """Распознать статус по имени enum или по русской метке."""
    value = value.strip()
    try:
        return TicketStatus(value)
    except ValueError:
        pass
    for status, label in STATUS_LABELS.items():
        if label.lower() == value.lower():
            return status
    raise ValueError(f"Неизвестный статус: {value!r}")


@admin_labeler.private_message(RegexRule(ADMIN_STATUS_PATTERN))
async def admin_status_handler(message: Message):
    match = re.match(ADMIN_STATUS_PATTERN, message.text or "", re.DOTALL)
    if not match or not await _operator_can_access(message.from_id, int(match.group(1))):
        await message.answer(
            "Заявка не найдена или недоступна.",
            keyboard=build_admin_keyboard(),
        )
        return
    try:
        new_status = _parse_status(match.group(2))
        ticket = await change_ticket_status(int(match.group(1)), new_status, str(message.from_id))
    except (ValueError, StatusTransitionError):
        await message.answer(
            "Неизвестный статус или недопустимый переход.\n"
            "Допустимые статусы: Новое, В обработке, Выполнено, Передано в администрацию, "
            "Передано в хозчасть.",
            keyboard=build_admin_keyboard(),
        )
        return
    await message.answer(
        f"Статус заявки #{match.group(1)} изменён: {status_label(ticket.status)}."
        if ticket
        else "Заявка не найдена.",
        keyboard=build_admin_keyboard(),
    )


# Алиасы для обратной совместимости
admin_panel_handler = admin_panel
admin_status_change_handler = admin_status_handler
