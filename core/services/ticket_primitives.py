"""Общие транзакционные и статусные примитивы ticket domain."""

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from core.database import async_session_maker
from core.models import MessageAuthorType, Ticket, TicketMessage, TicketStatus

ALLOWED_TRANSITIONS: dict[TicketStatus, set[TicketStatus]] = {
    TicketStatus.NEW: {
        TicketStatus.IN_PROGRESS,
        TicketStatus.TRANSFERRED_ADMIN,
        TicketStatus.TRANSFERRED_HOUSEKEEPING,
        TicketStatus.COMPLETED,
    },
    TicketStatus.IN_PROGRESS: {
        TicketStatus.TRANSFERRED_ADMIN,
        TicketStatus.TRANSFERRED_HOUSEKEEPING,
        TicketStatus.COMPLETED,
    },
    TicketStatus.TRANSFERRED_ADMIN: {
        TicketStatus.IN_PROGRESS,
        TicketStatus.COMPLETED,
    },
    TicketStatus.TRANSFERRED_HOUSEKEEPING: {
        TicketStatus.IN_PROGRESS,
        TicketStatus.COMPLETED,
    },
    TicketStatus.COMPLETED: {TicketStatus.IN_PROGRESS, TicketStatus.COMPLETED_AUTO},
    TicketStatus.COMPLETED_AUTO: {TicketStatus.IN_PROGRESS},
    TicketStatus.ANONYMOUS: {
        TicketStatus.IN_PROGRESS,
        TicketStatus.TRANSFERRED_ADMIN,
        TicketStatus.TRANSFERRED_HOUSEKEEPING,
        TicketStatus.COMPLETED,
    },
}

COMPLETED_STATUSES = {TicketStatus.COMPLETED, TicketStatus.COMPLETED_AUTO}


class StatusTransitionError(ValueError):
    """Недопустимый переход статуса заявки."""


def can_transition(current: TicketStatus, new: TicketStatus) -> bool:
    """Проверить, допустим ли переход статуса."""
    return new in ALLOWED_TRANSITIONS.get(current, set())


def validate_transition(current: TicketStatus, new: TicketStatus) -> None:
    """Выбросить StatusTransitionError, если переход недопустим."""
    if current == new:
        return
    if not can_transition(current, new):
        raise StatusTransitionError(
            f"Недопустимый переход статуса: {current.value} -> {new.value}"
        )


@asynccontextmanager
async def ticket_transaction():
    """Выполнить операцию заявки в транзакции с commit/rollback."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def add_ticket_message(
    session: AsyncSession,
    ticket: Ticket,
    author_type: MessageAuthorType,
    message: str,
    author_vk_id: int | None = None,
) -> TicketMessage:
    """Добавить сообщение в историю заявки без commit."""
    entry = TicketMessage(
        ticket_id=ticket.id,
        author_type=author_type,
        author_vk_id=author_vk_id,
        message=message,
    )
    session.add(entry)
    return entry
