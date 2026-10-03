"""Совместимый facade ticket domain для VK-бота и веб-панели."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from core.database import async_session_maker
from core.formatters.ticket_formatter import (
    format_ticket_details,
    format_ticket_list,
    mask_anonymous_data,
    status_label,
)
from core.models import (
    KnowledgeBase,
    Ticket,
    TicketMessage,
    TicketStatus,
)
from core.outbox import fire_outbox_delivery
from core.repositories.ticket_repo import TicketRepository
from core.services.ticket_admin_service import (
    assign_ticket_department as _assign_ticket_department,
)
from core.services.ticket_admin_service import (
    change_ticket_status as _change_ticket_status,
)
from core.services.ticket_admin_service import (
    reply_to_ticket as _reply_to_ticket,
)
from core.services.ticket_knowledge_service import find_knowledge_entry as _find_knowledge_entry
from core.services.ticket_primitives import (
    ALLOWED_TRANSITIONS,
    COMPLETED_STATUSES,
    StatusTransitionError,
    add_ticket_message,
    can_transition,
    ticket_transaction,
    validate_transition,
)
from core.services.ticket_routing_service import (
    _match_department_in_memory,
    _resolve_department,
    keyword_matches,
    sync_unassigned_ticket_departments,
)
from core.services.ticket_student_service import (
    add_student_reply as _add_student_reply,
)
from core.services.ticket_student_service import (
    create_ticket as _create_ticket,
)

logger = logging.getLogger(__name__)

# === Остальной код ===


async def get_user_tickets(
    vk_id: int,
    include_completed: bool = False,
    limit: int = 10,
    offset: int = 0,
) -> list[Ticket]:
    """Получить заявки пользователя по его VK ID (новые сверху).

    limit/offset дают пагинацию в боте («Показать ещё») и веб-панели.
    """
    async with async_session_maker() as session:
        return await TicketRepository(session).list_for_user(
            vk_id,
            include_completed=include_completed,
            limit=limit,
            offset=offset,
            completed_statuses=COMPLETED_STATUSES,
        )


async def get_user_ticket_by_id(vk_id: int, ticket_id: int) -> Ticket | None:
    """Получить заявку конкретного пользователя по глобальному ticket_id с проверкой владельца."""
    async with async_session_maker() as session:
        return await TicketRepository(session).get_for_user(vk_id, ticket_id)


async def get_user_ticket_local_number(
    user_id: int, ticket_id: int, session: AsyncSession | None = None
) -> int:
    """Получить персональный (локальный) порядковый номер заявки для пользователя (1, 2, 3...)."""
    if session is not None:
        return await TicketRepository(session).local_number(user_id, ticket_id)
    async with async_session_maker() as s:
        return await TicketRepository(s).local_number(user_id, ticket_id)


async def get_user_tickets_mapping(vk_id: int) -> dict[int, int]:
    """Словарь соответствия {global_ticket_id: local_number} для пользователя."""
    async with async_session_maker() as session:
        return await TicketRepository(session).mapping_for_user(vk_id)


async def get_user_ticket_by_local_number(vk_id: int, local_num: int) -> tuple[Ticket | None, int]:
    """Найти заявку пользователя по её локальному порядковому номеру (1-indexed)."""
    if local_num <= 0:
        return None, local_num
    async with async_session_maker() as session:
        ticket = await TicketRepository(session).get_by_local_number(vk_id, local_num)
        return ticket, local_num


async def resolve_user_ticket(vk_id: int, num: int) -> tuple[Ticket | None, int]:
    """Разрешить номер заявки: сначала как локальный номер пользователя, затем фоллбэк на глобальный ID."""
    ticket, local_num = await get_user_ticket_by_local_number(vk_id, num)
    if ticket is not None:
        return ticket, local_num
    ticket = await get_user_ticket_by_id(vk_id, num)
    if ticket is not None and ticket.user_id is not None and ticket.id is not None:
        computed_local = await get_user_ticket_local_number(ticket.user_id, ticket.id)
        return ticket, computed_local
    return None, num


async def get_ticket_messages(ticket_id: int) -> list[TicketMessage]:
    """Получить историю переписки по заявке."""
    async with async_session_maker() as session:
        return await TicketRepository(session).list_messages(ticket_id)


async def reply_to_ticket(
    ticket_id: int,
    admin_username: str,
    message: str,
    complete: bool = False,
) -> tuple[Ticket | None, bool]:
    """Ответить на заявку, сохраняя прежний публичный контракт."""
    return await _reply_to_ticket(
        ticket_id,
        admin_username,
        message,
        complete,
        delivery_callback=fire_outbox_delivery,
    )


async def change_ticket_status(
    ticket_id: int,
    new_status: TicketStatus,
    admin_username: str,
) -> Ticket | None:
    """Сменить статус заявки через admin service."""
    return await _change_ticket_status(
        ticket_id,
        new_status,
        admin_username,
        delivery_callback=fire_outbox_delivery,
    )


async def assign_ticket_department(
    ticket_id: int,
    department_id: int,
    admin_username: str,
) -> Ticket | None:
    """Переназначить заявку через admin service."""
    return await _assign_ticket_department(ticket_id, department_id, admin_username)


# === Создание обращений ===


async def create_ticket(
    topic: str,
    description: str,
    vk_id: int | None = None,
    keep_identity: bool = False,
    department_name: str | None = None,
) -> Ticket:
    """Создать заявку через student service, сохраняя прежний API."""
    return await _create_ticket(
        topic,
        description,
        vk_id,
        keep_identity,
        department_name,
        delivery_callback=fire_outbox_delivery,
    )


async def add_student_reply(ticket_id: int, vk_id: int, message: str) -> Ticket | None:
    """Добавить ответ студента с прежней сигнатурой facade."""
    return await _add_student_reply(
        ticket_id,
        vk_id,
        message,
        delivery_callback=fire_outbox_delivery,
    )


# === База знаний: единая логика поиска для бота и веб-панели ===


async def find_knowledge_entry(
    session: AsyncSession,
    description: str,
    department_name: str | None = None,
) -> KnowledgeBase | None:
    """Поиск по БЗ, оставленный в facade для обратной совместимости."""
    return await _find_knowledge_entry(session, description, department_name)


__all__ = [
    "ALLOWED_TRANSITIONS",
    "COMPLETED_STATUSES",
    "StatusTransitionError",
    "add_student_reply",
    "add_ticket_message",
    "assign_ticket_department",
    "can_transition",
    "change_ticket_status",
    "create_ticket",
    "find_knowledge_entry",
    "format_ticket_details",
    "format_ticket_list",
    "get_ticket_messages",
    "get_user_ticket_by_id",
    "get_user_ticket_by_local_number",
    "get_user_ticket_local_number",
    "get_user_tickets",
    "get_user_tickets_mapping",
    "keyword_matches",
    "mask_anonymous_data",
    "reply_to_ticket",
    "resolve_user_ticket",
    "status_label",
    "sync_unassigned_ticket_departments",
    "ticket_transaction",
    "validate_transition",
    "_match_department_in_memory",
    "_resolve_department",
]
