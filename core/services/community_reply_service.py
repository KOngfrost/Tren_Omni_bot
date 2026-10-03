"""Сервис обработки ответов администраторов, отправленных напрямую из сообщества ВКонтакте.

Когда человек отвечает студенту через диалоги группы VK (событие message_reply):
1. Находится активная заявка студента.
2. Статус заявки автоматически переводится в «В обработке» (IN_PROGRESS).
3. Ответ фиксируется в истории переписки по заявке (TicketMessage).
4. Действие журналируется в аудите (Log).
"""

import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Log, MessageAuthorType, Ticket, TicketMessage, TicketStatus, User
from core.services.ticket_primitives import (
    COMPLETED_STATUSES,
    add_ticket_message,
    ticket_transaction,
)

logger = logging.getLogger(__name__)

TICKET_PREFIX_RE = re.compile(
    r"^(?:\[?\s*(?:заявка|обращение|тикет)\s*[#№]?\s*(\d+)\s*\]?|[#№]\s*(\d+)|\[#(\d+)\])[\s:.,\-—–]*(.*)$",
    re.IGNORECASE | re.DOTALL,
)

TICKET_IN_TEXT_RE = re.compile(
    r"(?:заявка|обращение|тикет)?\s*[#№]\s*(\d+)",
    re.IGNORECASE,
)

BOT_SYSTEM_PATTERNS = re.compile(
    r"(?:ваше обращение принято|номер обращения\s*:\s*#?\d+|раздел\s*:|главное меню|выберите (?:отдел|действие|нужное действие)|не удалось отправить|введите текст обращения|анонимное обращение|спасибо за обращение)",
    re.IGNORECASE,
)


async def _find_ticket_by_id_or_local_num(
    session: AsyncSession,
    user_id: int,
    num: int,
) -> Ticket | None:
    """Найти заявку по ID или локальному номеру пользователя."""
    candidate = await session.scalar(
        select(Ticket).where(Ticket.id == num, Ticket.user_id == user_id).with_for_update()
    )
    if candidate is not None:
        return candidate

    user_tickets = (
        await session.scalars(
            select(Ticket)
            .where(Ticket.user_id == user_id)
            .order_by(Ticket.id.asc())
            .with_for_update()
        )
    ).all()
    if 1 <= num <= len(user_tickets):
        return user_tickets[num - 1]

    return await session.scalar(select(Ticket).where(Ticket.id == num).with_for_update())


async def _resolve_by_prefix(
    session: AsyncSession,
    user_id: int,
    text: str,
) -> tuple[Ticket | None, str]:
    """Шаг 1: Поиск по номеру заявки в начале сообщения."""
    m_prefix = TICKET_PREFIX_RE.match(text)
    if not m_prefix:
        return None, text

    raw_num = m_prefix.group(1) or m_prefix.group(2) or m_prefix.group(3)
    if not raw_num:
        return None, text

    num = int(raw_num)
    clean_body = (m_prefix.group(4) or "").strip()
    final_text = clean_body if clean_body else text

    candidate = await _find_ticket_by_id_or_local_num(session, user_id, num)
    if candidate:
        logger.info("Заявка #%d определена по префиксу в тексте ответа", candidate.id)
        return candidate, final_text

    return None, text


def _collect_quoted_messages(
    reply_message: dict | None,
    fwd_messages: list | None,
) -> list[dict]:
    """Собрать цепочку цитируемых и пересланных сообщений."""
    items: list[dict] = []
    curr = reply_message
    depth = 0
    while curr and isinstance(curr, dict) and depth < 4:
        items.append(curr)
        curr = curr.get("reply_message")
        depth += 1

    if fwd_messages and isinstance(fwd_messages, list):
        items.extend(fwd for fwd in fwd_messages if isinstance(fwd, dict))

    return items


async def _resolve_by_quoted_text(
    session: AsyncSession,
    user_id: int,
    q_text: str,
    text: str,
) -> tuple[Ticket | None, str] | None:
    """Поиск заявки по тексту одного цитируемого сообщения."""
    # 2.1. Номер заявки внутри цитаты
    m_num = TICKET_IN_TEXT_RE.search(q_text)
    if m_num:
        num = int(m_num.group(1))
        candidate = await _find_ticket_by_id_or_local_num(session, user_id, num)
        if candidate:
            logger.info(
                "Заявка #%d определена по номеру внутри цитаты: %s", candidate.id, q_text[:60]
            )
            return candidate, text

    user_tickets = (
        await session.scalars(
            select(Ticket)
            .where(Ticket.user_id == user_id)
            .order_by(Ticket.created_at.desc())
            .with_for_update()
        )
    ).all()

    # 2.2. Совпадение с описанием заявки
    for t in user_tickets:
        desc = (t.description or "").strip()
        if desc and (desc == q_text or desc in q_text or q_text in desc):
            logger.info("Заявка #%d определена по совпадению с описанием: %s", t.id, q_text[:60])
            return t, text

    # 2.3. Совпадение с сообщениями в истории заявки (TicketMessage)
    msg_match = await session.scalar(
        select(TicketMessage)
        .join(Ticket, TicketMessage.ticket_id == Ticket.id)
        .where(
            Ticket.user_id == user_id,
            (TicketMessage.message == q_text) | (TicketMessage.message.ilike(f"%{q_text[:100]}%")),
        )
        .order_by(TicketMessage.id.desc())
        .limit(1)
    )
    if msg_match and msg_match.ticket_id:
        candidate = await session.scalar(
            select(Ticket).where(Ticket.id == msg_match.ticket_id).with_for_update()
        )
        if candidate:
            logger.info(
                "Заявка #%d определена по совпадению с сообщением: %s", candidate.id, q_text[:60]
            )
            return candidate, text

    # 2.4. Совпадение с последним ответом
    for t in user_tickets:
        resp = (t.response_text or "").strip()
        if resp and (resp == q_text or resp in q_text or q_text in resp):
            logger.info(
                "Заявка #%d определена по совпадению с response_text: %s", t.id, q_text[:60]
            )
            return t, text

    return None


async def _resolve_fallback(
    session: AsyncSession,
    user_id: int,
    text: str,
) -> tuple[Ticket | None, str]:
    """Шаг 3: Фоллбэк на последнюю открытую или последнюю созданную заявку."""
    active_ticket = await session.scalar(
        select(Ticket)
        .where(
            Ticket.user_id == user_id,
            Ticket.status.not_in(COMPLETED_STATUSES),
        )
        .order_by(Ticket.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if active_ticket:
        logger.info("Целевая заявка #%d выбрана по фоллбэку (активная)", active_ticket.id)
        return active_ticket, text

    latest_ticket = await session.scalar(
        select(Ticket)
        .where(Ticket.user_id == user_id)
        .order_by(Ticket.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if latest_ticket:
        logger.info("Целевая заявка #%d выбрана по фоллбэку (последняя)", latest_ticket.id)
        return latest_ticket, text

    return None, text


async def resolve_target_ticket(
    session: AsyncSession,
    user_id: int,
    text: str,
    reply_message: dict | None = None,
    fwd_messages: list | None = None,
) -> tuple[Ticket | None, str]:
    """Определить целевую заявку студента для ответа администратора."""
    # 1. Поиск по префиксу с номером заявки
    candidate, final_text = await _resolve_by_prefix(session, user_id, text)
    if candidate is not None:
        return candidate, final_text

    # 2. Поиск по цитируемому сообщению
    quoted_items = _collect_quoted_messages(reply_message, fwd_messages)
    for q in quoted_items:
        q_text = (q.get("text") or "").strip()
        if not q_text:
            continue
        res = await _resolve_by_quoted_text(session, user_id, q_text, text)
        if res is not None:
            return res

    # 3. Фоллбэк
    return await _resolve_fallback(session, user_id, text)


async def _resolve_admin_name(session: AsyncSession, admin_author_id: int | None) -> str:
    """Определить читаемое имя администратора ВК."""
    if not admin_author_id:
        return "Сообщество VK"

    admin_user = await session.scalar(select(User).where(User.vk_id == admin_author_id))
    if admin_user and admin_user.full_name:
        return admin_user.full_name

    from core.vk_client import fetch_vk_user_name

    fetched_name = await fetch_vk_user_name(admin_author_id)
    if fetched_name:
        if admin_user:
            admin_user.full_name = fetched_name
        return fetched_name
    return f"VK ID {admin_author_id}"


async def handle_community_message_reply(
    peer_id: int,
    text: str,
    admin_author_id: int | None = None,
    reply_message: dict | None = None,
    fwd_messages: list | None = None,
) -> Ticket | None:
    """Обработать ответ администратора со стороны сообщества ВКонтакте."""
    if not peer_id or not text or not text.strip():
        return None

    clean_text = text.strip()

    # 1. Проверяем, не является ли это системным автоответом бота:
    if BOT_SYSTEM_PATTERNS.search(clean_text):
        logger.debug(
            "Пропуск системного сообщения бота (паттерн системного автоответа): %s",
            clean_text[:40],
        )
        return None

    # 2. Если admin_author_id отсутствует или <= 0 — это сообщение отправлено ботом через API,
    # а не живым администратором сообщества в интерфейсе ВКонтакте:
    if not admin_author_id or admin_author_id <= 0:
        logger.debug("Пропуск сообщения сообщества: отправлено ботом/API без admin_author_id")
        return None

    async with ticket_transaction() as session:
        # 3. Ищем пользователя в системе
        user = await session.scalar(select(User).where(User.vk_id == peer_id))
        if user is None:
            logger.info("Пользователь vk_id=%d не найден в БД при ответе сообщества", peer_id)
            return None

        # 4. Находим конкретную целевую заявку студента
        ticket, final_text = await resolve_target_ticket(
            session=session,
            user_id=user.id,
            text=clean_text,
            reply_message=reply_message,
            fwd_messages=fwd_messages,
        )
        if ticket is None:
            logger.info("Для пользователя vk_id=%d не удалось определить заявку", peer_id)
            return None

        admin_name = await _resolve_admin_name(session, admin_author_id)

        # Фиксируем сообщение оператора в истории заявки
        add_ticket_message(
            session,
            ticket,
            MessageAuthorType.ADMIN,
            final_text,
            author_vk_id=admin_author_id,
        )
        ticket.response_text = final_text

        # Автоматический перевод статуса в «В обработке»
        if ticket.status != TicketStatus.IN_PROGRESS:
            old_status = ticket.status
            ticket.status = TicketStatus.IN_PROGRESS
            add_ticket_message(
                session,
                ticket,
                MessageAuthorType.SYSTEM,
                f"Статус автоматически изменён: {old_status.value} -> {ticket.status.value} "
                f"(ответ со стороны сообщества, {admin_name})",
            )

        session.add(
            Log(
                user_id=ticket.user_id,
                action="community_reply",
                details=(
                    f"Ответ со стороны сообщества ({admin_name}) на заявку #{ticket.id} "
                    f"(статус: {ticket.status.value}): {final_text[:120]}"
                ),
            )
        )
        logger.info(
            "Ответ со стороны сообщества привязан к заявке #%d, статус: %s",
            ticket.id,
            ticket.status.value,
        )
        from core.events import notify_ticket_change

        notify_ticket_change()
        return ticket
