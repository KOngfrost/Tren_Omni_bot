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
    BroadcastStates,
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
    build_broadcast_comment_choice_keyboard,
    build_broadcast_confirm_keyboard,
    build_broadcast_exclude_keyboard,
    build_cancel_keyboard,
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
    BROADCAST_DIRECT_PATTERN,
    COMMANDS_ADMIN,
    COMMANDS_ADMIN_TICKETS,
    COMMANDS_BROADCAST,
    COMMANDS_CANCEL,
    COMMANDS_CONFIRM_BROADCAST,
    COMMANDS_REGULAR_MENU,
)
from core.database import async_session_maker
from core.heartbeat import touch_heartbeat
from core.models import MessageAuthorType, Ticket, TicketStatus
from core.reporting import get_app_tz
from core.services.broadcast_service import (
    enqueue_broadcast,
    get_recent_users,
    get_total_users_count,
    parse_vk_post_reference,
)
from core.ticket_service import (
    COMPLETED_STATUSES,
    StatusTransitionError,
    change_ticket_status,
    get_ticket_messages,
    reply_to_ticket,
    status_label,
)

logger = logging.getLogger(__name__)

admin_labeler = BotLabeler()


@admin_labeler.private_message(text=COMMANDS_ADMIN)
async def admin_panel(message: Message):
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    if not await BotCore.is_admin(user):
        logger.warning(
            "Отклонён неавторизованный запрос к админ-панели VK от vk_id=%s", message.from_id
        )
        return

    await BotCore.log_action(user, "admin_panel", "Открыта админ-панель")
    async with async_session_maker() as session:
        is_super, _ = await get_admin_scope_for_vk_id(session, message.from_id)
    if BotCore.is_admin_vk_id(message.from_id):
        is_super = True

    desc_lines = [
        "👔 Админ-панель\n",
        "Доступные функции:",
        "• Просмотр и решение заявок студентов за сегодняшний день",
        "• Формирование оперативных отчётов",
    ]
    if is_super:
        desc_lines.append("• Массовая рассылка сообщений последним пользователям")
    desc_lines.append("\nНажмите «Заявки администратора» для работы с обращениями.")

    await message.answer(
        "\n".join(desc_lines),
        keyboard=build_admin_keyboard(is_super=is_super),
    )


@admin_labeler.private_message(text=COMMANDS_REGULAR_MENU)
async def regular_menu_handler(message: Message):
    touch_heartbeat()
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    departments = await _get_department_names()
    keyboard = build_main_keyboard(
        await BotCore.is_admin(user),
        departments,
        is_tech_admin=BotCore.is_tech_admin_vk_id(message.from_id),
    )
    await message.answer(
        _main_reply_text(departments),
        keyboard=keyboard,
    )


async def _render_admin_tickets_page(message: Message, page: int, meta: dict[str, Any]) -> None:
    """Отрисовать страницу списка активных заявок администратора за сегодняшний день (пагинация)."""
    async with async_session_maker() as session:
        is_super, dept_id = await get_admin_scope_for_vk_id(session, message.from_id)

    if not is_super and dept_id is None:
        logger.warning("Отклонён запрос к заявкам администратора от vk_id=%s", message.from_id)
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
        if not is_super:
            if dept_id is not None:
                stmt = stmt.where(Ticket.department_id == dept_id)
            else:
                stmt = stmt.where(Ticket.department_id.is_(None))

        count_stmt = select(func.count(Ticket.id)).where(
            Ticket.created_at >= today_start,
            Ticket.status.not_in((TicketStatus.COMPLETED, TicketStatus.COMPLETED_AUTO)),
        )
        total_count_stmt = select(func.count(Ticket.id)).where(Ticket.created_at >= today_start)
        if not is_super:
            if dept_id is not None:
                count_stmt = count_stmt.where(Ticket.department_id == dept_id)
                total_count_stmt = total_count_stmt.where(Ticket.department_id == dept_id)
            else:
                count_stmt = count_stmt.where(Ticket.department_id.is_(None))
                total_count_stmt = total_count_stmt.where(Ticket.department_id.is_(None))

        pending_count = await session.scalar(count_stmt)
        total_count = await session.scalar(total_count_stmt)
        tickets = list((await session.scalars(stmt)).all())

    page = clamp_page(page, len(tickets))
    await persist_page(message.from_id, KIND_ADMIN_TICKETS, page, meta)

    if not tickets:
        await message.answer(
            f"💭 Заявок за сегодня ({today_str}) пока нет.\n\n"
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
            f"⭕️ Заявка #{ticket_id} не найдена или относится к другому отделу.",
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
        await message.answer("⭕️ Заявка не найдена.", keyboard=build_admin_keyboard())
        return

    messages = await get_ticket_messages(ticket_id)
    dept_name = ticket.department.name if ticket.department else "Без отдела"
    student_name = (
        "Аноним"
        if ticket.is_anonymous
        else (
            f"{ticket.user.full_name} (vk.com/id{ticket.user.vk_id})"
            if ticket.user and ticket.user.full_name
            else (
                f"VK ID {ticket.user.vk_id} (vk.com/id{ticket.user.vk_id})"
                if ticket.user and ticket.user.vk_id
                else "—"
            )
        )
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
            lines.append(
                f"• [{m_time}] {author}: {m.message[:120]}{'...' if len(m.message) > 120 else ''}"
            )
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
        await message.answer(
            "⭕️ Заявка не найдена или недоступна.", keyboard=build_admin_keyboard()
        )
        return

    async with async_session_maker() as session:
        ticket = await session.scalar(
            select(Ticket).options(selectinload(Ticket.department)).where(Ticket.id == ticket_id)
        )

    if ticket is None:
        await message.answer("⭕️ Заявка не найдена.", keyboard=build_admin_keyboard())
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
        await message.answer(
            "⭕️ Заявка не найдена или недоступна.", keyboard=build_admin_keyboard()
        )
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
            build_admin_ticket_actions_keyboard(ticket_id) if ticket_id else build_admin_keyboard()
        )
        await message.answer("❌️ Ввод ответа отменён.", keyboard=keyboard)
        return

    if not ticket_id:
        await vk_bot.state_dispenser.delete(message.from_id)
        await message.answer(
            "⭕️ Сессия ответа истекла. Пожалуйста, откройте заявку заново.",
            keyboard=build_admin_keyboard(),
        )
        return

    if len(raw_text) < 2:
        await message.answer(
            "‼️ Текст ответа слишком короткий. Введите ответ подробнее или нажмите «Отмена»:"
        )
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
        await message.answer("⭕️ Заявка не найдена.", keyboard=build_admin_keyboard())
        return

    note = (
        " (уведомление отправлено студенту в VK)"
        if delivered
        else " (заявка анонимная или без VK ID)"
    )
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
        await message.answer(
            "⭕️ Заявка не найдена или недоступна.", keyboard=build_admin_keyboard()
        )
        return

    try:
        ticket = await change_ticket_status(
            ticket_id, TicketStatus.COMPLETED, f"VK:{message.from_id}"
        )
    except (ValueError, StatusTransitionError) as e:
        await message.answer(
            f"⭕️ Не удалось завершить заявку: {e}", keyboard=build_admin_keyboard()
        )
        return

    if ticket is None:
        await message.answer("⭕️ Заявка не найдена.", keyboard=build_admin_keyboard())
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
        await message.answer(
            "⭕️ Заявка не найдена или недоступна.", keyboard=build_admin_keyboard()
        )
        return

    try:
        ticket = await change_ticket_status(
            ticket_id, TicketStatus.IN_PROGRESS, f"VK:{message.from_id}"
        )
    except (ValueError, StatusTransitionError) as e:
        await message.answer(
            f"⭕️ Не удалось изменить статус: {e}", keyboard=build_admin_keyboard()
        )
        return

    if ticket is None:
        await message.answer("⭕️ Заявка не найдена.", keyboard=build_admin_keyboard())
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
            "⭕️ Заявка не найдена или недоступна.",
            keyboard=keyboard,
        )
        return
    try:
        new_status = _parse_status(match.group(2))
        ticket = await change_ticket_status(int(match.group(1)), new_status, str(message.from_id))
    except (ValueError, StatusTransitionError):
        await message.answer(
            "⭕️ Неизвестный статус или недопустимый переход.\n"
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
        f"✅️ Статус заявки #{match.group(1)} изменён: {status_label(ticket.status)}."
        if ticket
        else "Заявка не найдена.",
        keyboard=build_admin_keyboard(),
    )


# --- Массовая рассылка сообщений (SUPERADMIN) ---


@admin_labeler.private_message(text=COMMANDS_BROADCAST)
async def broadcast_start_handler(message: Message):
    """Начало сценария рассылки: запрос числа получателей N."""
    touch_heartbeat()
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    if not await BotCore.is_admin(user):
        return

    async with async_session_maker() as session:
        is_super, _ = await get_admin_scope_for_vk_id(session, message.from_id)
    if not is_super and not BotCore.is_admin_vk_id(message.from_id):
        return

    from bots.vk.bot import vk_bot

    async with async_session_maker() as session:
        total = await get_total_users_count(session)

    await vk_bot.state_dispenser.set(message.from_id, BroadcastStates.WAITING_COUNT)
    await message.answer(
        "Массовая рассылка сообщений\n\n"
        f"Всего пользователей в системе: {total}\n\n"
        "Укажите число пользователей (N), которым нужно отправить сообщение:\n"
        "(например: 10, 25, 50)\n\n"
        "Сообщение будет отправлено N последним активным студентам.\n"
        "Для отмены нажмите «Отмена».",
        keyboard=build_cancel_keyboard(),
    )


def _parse_single_attachment(att: Any) -> str | None:
    """Извлечь wall- или photo-вложение из отдельного объекта вложения."""
    if att is None:
        return None
    att_type = getattr(att, "type", None) or (att.get("type") if isinstance(att, dict) else None)
    att_type_str = str(getattr(att_type, "value", att_type)).lower() if att_type else ""

    if att_type_str == "wall":
        wall_obj = getattr(att, "wall", None) or (
            att.get("wall") if isinstance(att, dict) else None
        )
        if wall_obj is None:
            wall_obj = att
        owner_id = (
            getattr(wall_obj, "owner_id", None)
            or getattr(wall_obj, "to_id", None)
            or (wall_obj.get("owner_id") if isinstance(wall_obj, dict) else None)
            or (wall_obj.get("to_id") if isinstance(wall_obj, dict) else None)
        )
        post_id = getattr(wall_obj, "id", None) or (
            wall_obj.get("id") if isinstance(wall_obj, dict) else None
        )
        if owner_id is not None and post_id is not None:
            return f"wall{owner_id}_{post_id}"

    elif att_type_str == "photo":
        photo_obj = getattr(att, "photo", None) or (
            att.get("photo") if isinstance(att, dict) else None
        )
        if photo_obj is None:
            photo_obj = att
        owner_id = getattr(photo_obj, "owner_id", None) or (
            photo_obj.get("owner_id") if isinstance(photo_obj, dict) else None
        )
        photo_id = getattr(photo_obj, "id", None) or (
            photo_obj.get("id") if isinstance(photo_obj, dict) else None
        )
        access_key = getattr(photo_obj, "access_key", None) or (
            photo_obj.get("access_key") if isinstance(photo_obj, dict) else None
        )
        if owner_id is not None and photo_id is not None:
            if access_key:
                return f"photo{owner_id}_{photo_id}_{access_key}"
            return f"photo{owner_id}_{photo_id}"

    return None


def _extract_from_attachment_list(attachments: Any) -> str | None:
    """Извлечь первое подходящее вложение из списка вложений."""
    if attachments and isinstance(attachments, list):
        for att in attachments:
            parsed = _parse_single_attachment(att)
            if parsed:
                return parsed
    return None


def _extract_from_submessage(submsg: Any) -> str | None:
    """Извлечь wall- или photo-вложение из пересланного или цитируемого сообщения."""
    if not submsg:
        return None
    atts = getattr(submsg, "attachments", None) or (
        submsg.get("attachments") if isinstance(submsg, dict) else None
    )
    from_atts = _extract_from_attachment_list(atts)
    if from_atts:
        return from_atts

    sub_text = getattr(submsg, "text", None) or (
        submsg.get("text") if isinstance(submsg, dict) else None
    )
    if sub_text:
        return parse_vk_post_reference(sub_text)
    return None


def _extract_wall_attachment(message: Message | Any) -> str | None:
    """Извлечь идентификатор вложения (wall или photo) из сообщения, пересланных сообщений или текста."""
    # 1. Прямые вложения сообщения
    from_direct = _extract_from_attachment_list(getattr(message, "attachments", None))
    if from_direct:
        return from_direct

    # 2. Пересланные сообщения (fwd_messages)
    fwd_messages = getattr(message, "fwd_messages", None)
    if fwd_messages and isinstance(fwd_messages, list):
        for fwd in fwd_messages:
            parsed = _extract_from_submessage(fwd)
            if parsed:
                return parsed

    # 3. Ответ на сообщение (reply_message)
    from_reply = _extract_from_submessage(getattr(message, "reply_message", None))
    if from_reply:
        return from_reply

    # 4. Текст самого сообщения (ссылка на стену)
    text = getattr(message, "text", None)
    if text:
        return parse_vk_post_reference(text)

    return None


def _format_confirm_message(
    effective_count: int,
    excluded_count: int,
    attachment: str | None,
    text: str,
) -> str:
    """Сформировать текст карточки подтверждения рассылки строго без эмодзи."""
    lines = ["Подтверждение рассылки\n"]
    if excluded_count > 0:
        lines.append(f"Получателей: {effective_count} (исключено: {excluded_count})")
    else:
        lines.append(f"Получателей: {effective_count} пользователей")

    if attachment:
        lines.append(f"Прикрепленный пост: {attachment}")
        lines.append(f"Текст: {text if text else 'без комментария'}")
    else:
        lines.append(f"Текст:\n«{text}»")

    lines.append("")
    lines.append("Сообщения будут добавлены в защищённую очередь Outbox и доставлены студентам.")
    lines.append("")
    lines.append("Подтвердить отправку?")
    return "\n".join(lines)


@admin_labeler.private_message(RegexRule(BROADCAST_DIRECT_PATTERN))
async def broadcast_direct_handler(message: Message):
    """Быстрая команда рассылки: Рассылка <N> <текст>."""
    touch_heartbeat()
    user = await BotCore.get_or_create_user(vk_id=message.from_id)
    if not await BotCore.is_admin(user):
        return

    async with async_session_maker() as session:
        is_super, _ = await get_admin_scope_for_vk_id(session, message.from_id)
    if not is_super and not BotCore.is_admin_vk_id(message.from_id):
        return

    match = re.match(BROADCAST_DIRECT_PATTERN, message.text or "", re.DOTALL)
    if not match:
        return

    try:
        count = int(match.group(1))
    except (ValueError, TypeError):
        count = 0
    text = match.group(2).strip()

    if count <= 0 or not text:
        await message.answer(
            "Некорректные параметры команды. Формат: Рассылка <число N> <текст сообщения>",
            keyboard=build_admin_keyboard(is_super=True),
        )
        return

    from bots.vk.bot import vk_bot

    await vk_bot.state_dispenser.set(
        message.from_id,
        BroadcastStates.WAITING_CONFIRM,
        count=count,
        text=text,
        excluded_ids=[],
    )
    confirm_msg = _format_confirm_message(
        effective_count=count,
        excluded_count=0,
        attachment=None,
        text=text,
    )
    await message.answer(
        confirm_msg,
        keyboard=build_broadcast_confirm_keyboard(),
    )


@admin_labeler.private_message(state=BroadcastStates.WAITING_COUNT)
async def broadcast_count_handler(message: Message):
    """Обработка ввода числа получателей N."""
    touch_heartbeat()
    text = (message.text or "").strip()
    from bots.vk.bot import vk_bot

    if text in COMMANDS_CANCEL:
        await vk_bot.state_dispenser.delete(message.from_id)
        await message.answer(
            "Рассылка отменена.",
            keyboard=build_admin_keyboard(is_super=True),
        )
        return

    try:
        count = int(text)
        if count <= 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "Пожалуйста, введите положительное целое число получателей (например, 10 или 50) "
            "или нажмите «Отмена»:",
            keyboard=build_cancel_keyboard(),
        )
        return

    async with async_session_maker() as session:
        users = await get_recent_users(session, limit=count)

    if not users:
        await message.answer(
            "В системе пока нет пользователей для отправки сообщений.",
            keyboard=build_admin_keyboard(is_super=True),
        )
        await vk_bot.state_dispenser.delete(message.from_id)
        return

    recipients = [
        {
            "id": u.id,
            "vk_id": u.vk_id,
            "name": u.full_name or f"id{u.vk_id}",
        }
        for u in users
    ]

    if count <= 10:
        await vk_bot.state_dispenser.set(
            message.from_id,
            BroadcastStates.WAITING_EXCLUDE,
            count=count,
            recipients=recipients,
            excluded_ids=[],
        )
        lines = [
            f"Выбрано пользователей: {len(recipients)}",
        ]
        for idx, r in enumerate(recipients, 1):
            lines.append(f"{idx}. {r['name']} (id{r['vk_id']})")
        lines.extend(
            [
                "",
                "Чтобы исключить пользователя, нажмите кнопку ниже или введите его номер.",
                "Для продолжения нажмите «Продолжить».",
            ]
        )
        await message.answer(
            "\n".join(lines),
            keyboard=build_broadcast_exclude_keyboard(len(recipients), set()),
        )
    else:
        await vk_bot.state_dispenser.set(
            message.from_id,
            BroadcastStates.WAITING_TEXT,
            count=count,
            recipients=recipients,
            excluded_ids=[],
        )
        await message.answer(
            f"Выбрано {count} последних пользователей.\n\n"
            "Введите текст сообщения для рассылки или перешлите пост из сообщества:\n\n"
            "Для отмены нажмите «Отмена».",
            keyboard=build_cancel_keyboard(),
        )


@admin_labeler.private_message(state=BroadcastStates.WAITING_EXCLUDE)
async def broadcast_exclude_handler(message: Message):
    """Обработка исключения получателей из списка рассылки."""
    touch_heartbeat()
    text = (message.text or "").strip()
    from bots.vk.bot import vk_bot

    if text in COMMANDS_CANCEL:
        await vk_bot.state_dispenser.delete(message.from_id)
        await message.answer(
            "Рассылка отменена.",
            keyboard=build_admin_keyboard(is_super=True),
        )
        return

    state_peer = await vk_bot.state_dispenser.get(message.from_id)
    payload = state_peer.payload if state_peer else {}
    recipients: list[dict[str, Any]] = list(payload.get("recipients", []))
    excluded_ids: list[int] = list(payload.get("excluded_ids", []))
    count = int(payload.get("count", len(recipients)))

    if text.lower() == "продолжить":
        effective = [r for r in recipients if r["id"] not in excluded_ids]
        if not effective:
            await message.answer(
                "Все получатели исключены. Некому отправлять рассылку. "
                "Восстановите хотя бы одного пользователя или нажмите «Отмена».",
                keyboard=build_broadcast_exclude_keyboard(
                    len(recipients),
                    {i for i, r in enumerate(recipients, 1) if r["id"] in excluded_ids},
                ),
            )
            return

        await vk_bot.state_dispenser.set(
            message.from_id,
            BroadcastStates.WAITING_TEXT,
            count=count,
            recipients=recipients,
            excluded_ids=excluded_ids,
        )
        await message.answer(
            "Введите текст сообщения для рассылки или перешлите пост из сообщества:\n\n"
            "Для отмены нажмите «Отмена».",
            keyboard=build_cancel_keyboard(),
        )
        return

    match = re.match(r"^(?:исключить|вернуть)?\s*#?(\d+)$", text, re.IGNORECASE)
    if match:
        idx = int(match.group(1))
        if 1 <= idx <= len(recipients):
            target = recipients[idx - 1]
            target_id = target["id"]
            if target_id in excluded_ids:
                excluded_ids.remove(target_id)
                prefix_msg = f"Пользователь #{idx} ({target['name']}) возвращен в список рассылки."
            else:
                excluded_ids.append(target_id)
                prefix_msg = f"Пользователь #{idx} ({target['name']}) исключен из рассылки."

            await vk_bot.state_dispenser.set(
                message.from_id,
                BroadcastStates.WAITING_EXCLUDE,
                count=count,
                recipients=recipients,
                excluded_ids=excluded_ids,
            )

            excluded_indices = {i for i, r in enumerate(recipients, 1) if r["id"] in excluded_ids}
            lines = [
                prefix_msg,
                "",
                f"Выбрано пользователей: {len(recipients)} (исключено: {len(excluded_ids)})",
            ]
            for i, r in enumerate(recipients, 1):
                status_str = " [Исключен]" if r["id"] in excluded_ids else ""
                lines.append(f"{i}. {r['name']} (id{r['vk_id']}){status_str}")
            lines.extend(
                [
                    "",
                    "Чтобы исключить пользователя, нажмите кнопку ниже или введите его номер.",
                    "Для продолжения нажмите «Продолжить».",
                ]
            )
            await message.answer(
                "\n".join(lines),
                keyboard=build_broadcast_exclude_keyboard(len(recipients), excluded_indices),
            )
            return

    excluded_indices = {i for i, r in enumerate(recipients, 1) if r["id"] in excluded_ids}
    await message.answer(
        "Пожалуйста, выберите пользователя для исключения или нажмите «Продолжить»:",
        keyboard=build_broadcast_exclude_keyboard(len(recipients), excluded_indices),
    )


@admin_labeler.private_message(state=BroadcastStates.WAITING_TEXT)
async def broadcast_text_handler(message: Message):
    """Обработка ввода текста рассылки или пересылки поста/фото."""
    touch_heartbeat()
    text = (message.text or "").strip()
    from bots.vk.bot import vk_bot

    if text in COMMANDS_CANCEL:
        await vk_bot.state_dispenser.delete(message.from_id)
        await message.answer(
            "Рассылка отменена.",
            keyboard=build_admin_keyboard(is_super=True),
        )
        return

    state_peer = await vk_bot.state_dispenser.get(message.from_id)
    payload = state_peer.payload if state_peer else {}
    count = int(payload.get("count", 1))
    recipients = list(payload.get("recipients", []))
    excluded_ids = list(payload.get("excluded_ids", []))
    adding_comment = bool(payload.get("adding_comment", False))

    if adding_comment:
        if not text:
            await message.answer(
                "Текст комментария не может быть пустым. Введите текст комментария или нажмите «Отмена»:",
                keyboard=build_cancel_keyboard(),
            )
            return

        attachment = payload.get("attachment")
        await vk_bot.state_dispenser.set(
            message.from_id,
            BroadcastStates.WAITING_CONFIRM,
            count=count,
            recipients=recipients,
            excluded_ids=excluded_ids,
            attachment=attachment,
            text=text,
        )
        effective_count = (
            len([r for r in recipients if r["id"] not in excluded_ids]) if recipients else count
        )
        msg_text = _format_confirm_message(
            effective_count=effective_count,
            excluded_count=len(excluded_ids),
            attachment=attachment,
            text=text,
        )
        await message.answer(
            msg_text,
            keyboard=build_broadcast_confirm_keyboard(),
        )
        return

    attachment = _extract_wall_attachment(message)
    if attachment:
        await vk_bot.state_dispenser.set(
            message.from_id,
            BroadcastStates.WAITING_COMMENT_CHOICE,
            count=count,
            recipients=recipients,
            excluded_ids=excluded_ids,
            attachment=attachment,
        )
        await message.answer(
            f"Прикреплен пост: {attachment}\n\nХотите добавить свой комментарий к рассылке?",
            keyboard=build_broadcast_comment_choice_keyboard(),
        )
        return

    if text:
        await vk_bot.state_dispenser.set(
            message.from_id,
            BroadcastStates.WAITING_CONFIRM,
            count=count,
            recipients=recipients,
            excluded_ids=excluded_ids,
            attachment=None,
            text=text,
        )
        effective_count = (
            len([r for r in recipients if r["id"] not in excluded_ids]) if recipients else count
        )
        msg_text = _format_confirm_message(
            effective_count=effective_count,
            excluded_count=len(excluded_ids),
            attachment=None,
            text=text,
        )
        await message.answer(
            msg_text,
            keyboard=build_broadcast_confirm_keyboard(),
        )
        return

    await message.answer(
        "Текст сообщения не может быть пустым. Введите текст, перешлите пост или нажмите «Отмена»:",
        keyboard=build_cancel_keyboard(),
    )


@admin_labeler.private_message(state=BroadcastStates.WAITING_COMMENT_CHOICE)
async def broadcast_comment_choice_handler(message: Message):
    """Обработка выбора добавления комментария к прикрепленному посту/фото."""
    touch_heartbeat()
    text = (message.text or "").strip()
    from bots.vk.bot import vk_bot

    if text in COMMANDS_CANCEL:
        await vk_bot.state_dispenser.delete(message.from_id)
        await message.answer(
            "Рассылка отменена.",
            keyboard=build_admin_keyboard(is_super=True),
        )
        return

    state_peer = await vk_bot.state_dispenser.get(message.from_id)
    payload = state_peer.payload if state_peer else {}
    count = int(payload.get("count", 1))
    recipients = list(payload.get("recipients", []))
    excluded_ids = list(payload.get("excluded_ids", []))
    attachment = payload.get("attachment")

    normalized = text.lower()
    if normalized == "отправить без комментария":
        await vk_bot.state_dispenser.set(
            message.from_id,
            BroadcastStates.WAITING_CONFIRM,
            count=count,
            recipients=recipients,
            excluded_ids=excluded_ids,
            attachment=attachment,
            text="",
        )
        effective_count = (
            len([r for r in recipients if r["id"] not in excluded_ids]) if recipients else count
        )
        msg_text = _format_confirm_message(
            effective_count=effective_count,
            excluded_count=len(excluded_ids),
            attachment=attachment,
            text="",
        )
        await message.answer(
            msg_text,
            keyboard=build_broadcast_confirm_keyboard(),
        )
        return

    if normalized == "добавить комментарий":
        await vk_bot.state_dispenser.set(
            message.from_id,
            BroadcastStates.WAITING_TEXT,
            count=count,
            recipients=recipients,
            excluded_ids=excluded_ids,
            attachment=attachment,
            adding_comment=True,
        )
        await message.answer(
            "Введите текст комментария к посту:\n\nДля отмены нажмите «Отмена».",
            keyboard=build_cancel_keyboard(),
        )
        return

    await message.answer(
        "Пожалуйста, выберите действие кнопками ниже или нажмите «Отмена»:",
        keyboard=build_broadcast_comment_choice_keyboard(),
    )


@admin_labeler.private_message(state=BroadcastStates.WAITING_CONFIRM)
async def broadcast_confirm_handler(message: Message):
    """Подтверждение и отправка рассылки."""
    touch_heartbeat()
    text_input = (message.text or "").strip()
    from bots.vk.bot import vk_bot

    if text_input in COMMANDS_CANCEL:
        await vk_bot.state_dispenser.delete(message.from_id)
        await message.answer(
            "Рассылка отменена.",
            keyboard=build_admin_keyboard(is_super=True),
        )
        return

    if text_input not in COMMANDS_CONFIRM_BROADCAST:
        await message.answer(
            "Пожалуйста, нажмите «Подтвердить рассылку» для отправки или «Отмена» для возврата:",
            keyboard=build_broadcast_confirm_keyboard(),
        )
        return

    state_peer = await vk_bot.state_dispenser.get(message.from_id)
    payload = state_peer.payload if state_peer else {}
    count = int(payload.get("count", 1))
    broadcast_text = str(payload.get("text", ""))
    attachment = payload.get("attachment")
    excluded_ids = list(payload.get("excluded_ids", []))

    await vk_bot.state_dispenser.delete(message.from_id)

    async with async_session_maker() as session:
        result = await enqueue_broadcast(
            session=session,
            text=broadcast_text,
            count=count,
            excluded_user_ids=excluded_ids if excluded_ids else None,
            attachment=attachment,
            author_name=f"vk_admin_{message.from_id}",
            actor_type="bot",
        )

    if result.status == "success":
        await message.answer(
            f"Рассылка успешно запущена.\n\n"
            f"Поставлено в очередь отправки: {result.recipients_count} пользователей.\n"
            f"Сообщения доставляются адресатам через фоновый воркер.",
            keyboard=build_admin_keyboard(is_super=True),
        )
    else:
        await message.answer(
            f"Ошибка при отправке рассылки: {result.error}",
            keyboard=build_admin_keyboard(is_super=True),
        )


# Рендерер пагинации списка заявок администратора (ЭТАП 4.1 / B4)
register_renderer(KIND_ADMIN_TICKETS, _render_admin_tickets_page)

# Алиасы для обратной совместимости
admin_panel_handler = admin_panel
admin_status_change_handler = admin_status_handler
