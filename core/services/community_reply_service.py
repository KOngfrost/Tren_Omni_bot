"""Сервис обработки ответов администраторов, отправленных напрямую из сообщества ВКонтакте.

Когда человек отвечает студенту через диалоги группы VK (событие message_reply):
1. Находится активная заявка студента.
2. Статус заявки автоматически переводится в «В обработке» (IN_PROGRESS).
3. Ответ фиксируется в истории переписки по заявке (TicketMessage).
4. Действие журналируется в аудите (Log).
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from core.models import Log, MessageAuthorType, Ticket, TicketStatus, User, VkOutbox
from core.services.ticket_primitives import (
    COMPLETED_STATUSES,
    add_ticket_message,
    ticket_transaction,
)

logger = logging.getLogger(__name__)


async def handle_community_message_reply(
    peer_id: int,
    text: str,
    admin_author_id: int | None = None,
) -> Ticket | None:
    """Обработать ответ администратора со стороны сообщества ВКонтакте."""
    if not peer_id or not text or not text.strip():
        return None

    clean_text = text.strip()

    async with ticket_transaction() as session:
        # Проверяем, не является ли это исходящим сообщением самого бота:
        # Если admin_author_id отсутствует, сообщение отправлено через API (возможно, ботом).
        if admin_author_id is None:
            now_utc = datetime.now(UTC)
            cutoff = now_utc - timedelta(seconds=60)
            recent_outbox = await session.scalar(
                select(VkOutbox)
                .where(
                    VkOutbox.vk_id == peer_id,
                    VkOutbox.created_at >= cutoff,
                )
                .order_by(VkOutbox.id.desc())
                .limit(1)
            )
            if recent_outbox and (
                clean_text == recent_outbox.text.strip()
                or recent_outbox.text.strip().startswith(clean_text)
            ):
                logger.debug("Пропуск исходящего сообщения бота (совпадение с VkOutbox)")
                return None

        # Ищем пользователя в системе
        user = await session.scalar(select(User).where(User.vk_id == peer_id))
        if user is None:
            logger.info("Пользователь vk_id=%d не найден в БД при ответе сообщества", peer_id)
            return None

        # Ищем последнюю активную заявку пользователя
        stmt = (
            select(Ticket)
            .where(
                Ticket.user_id == user.id,
                Ticket.status.not_in(COMPLETED_STATUSES),
            )
            .order_by(Ticket.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        ticket = await session.scalar(stmt)
        if ticket is None:
            logger.info("У пользователя vk_id=%d нет активных незавершённых заявок", peer_id)
            return None

        admin_name = "Сообщество VK"
        if admin_author_id:
            admin_user = await session.scalar(select(User).where(User.vk_id == admin_author_id))
            if admin_user and admin_user.full_name:
                admin_name = admin_user.full_name
            else:
                from core.vk_client import fetch_vk_user_name

                fetched_name = await fetch_vk_user_name(admin_author_id)
                if fetched_name:
                    admin_name = fetched_name
                    if admin_user:
                        admin_user.full_name = fetched_name
                else:
                    admin_name = f"VK ID {admin_author_id}"

        # Фиксируем сообщение оператора в истории заявки
        add_ticket_message(
            session,
            ticket,
            MessageAuthorType.ADMIN,
            clean_text,
            author_vk_id=admin_author_id,
        )
        ticket.response_text = clean_text

        # Автоматический перевод статуса в «В обработке»
        if ticket.status not in COMPLETED_STATUSES and ticket.status != TicketStatus.IN_PROGRESS:
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
                    f"(статус: {ticket.status.value}): {clean_text[:120]}"
                ),
            )
        )
        logger.info(
            "Ответ со стороны сообщества привязан к заявке #%d, статус: %s",
            ticket.id,
            ticket.status.value,
        )
        return ticket
