"""Бизнес-операции администратора над заявками."""

from collections.abc import Callable

from core.formatters.ticket_formatter import status_label
from core.models import Log, MessageAuthorType, Ticket, TicketStatus
from core.outbox import add_outbox_message, fire_outbox_delivery
from core.repositories.ticket_repo import TicketRepository
from core.services.ticket_primitives import (
    COMPLETED_STATUSES,
    add_ticket_message,
    ticket_transaction,
    validate_transition,
)


async def reply_to_ticket(
    ticket_id: int,
    admin_username: str,
    message: str,
    complete: bool = False,
    *,
    delivery_callback: Callable[[], None] | None = None,
) -> tuple[Ticket | None, bool]:
    """Ответ администратора с атомарной записью изменения и outbox."""
    async with ticket_transaction() as session:
        repository = TicketRepository(session)
        ticket = await repository.lock_ticket(ticket_id)
        if ticket is None:
            return None, False

        add_ticket_message(session, ticket, MessageAuthorType.ADMIN, message)
        ticket.response_text = message
        if complete and ticket.status not in COMPLETED_STATUSES:
            validate_transition(ticket.status, TicketStatus.COMPLETED)
            ticket.status = TicketStatus.COMPLETED
        elif ticket.status not in COMPLETED_STATUSES:
            ticket.status = TicketStatus.IN_PROGRESS

        session.add(
            Log(
                user_id=ticket.user_id,
                action="web_reply",
                details=(
                    f"Администратор {admin_username} ответил на заявку #{ticket.id}"
                    f" (статус: {ticket.status.value})"
                ),
            )
        )

        scheduled = False
        if not ticket.is_anonymous and ticket.user and ticket.user.vk_id:
            local_num = await repository.local_number(ticket.user_id, ticket.id)
            dept_name = ticket.department.name if ticket.department else "—"
            add_outbox_message(
                session,
                ticket.user.vk_id,
                f"Ответ на вашу заявку #{local_num} ({dept_name}):\n\n{message}",
            )
            scheduled = True

    if scheduled:
        (delivery_callback or fire_outbox_delivery)()
    return ticket, scheduled


async def change_ticket_status(
    ticket_id: int,
    new_status: TicketStatus,
    admin_username: str,
    *,
    delivery_callback: Callable[[], None] | None = None,
) -> Ticket | None:
    """Сменить статус заявки с проверкой перехода и журналированием."""
    async with ticket_transaction() as session:
        repository = TicketRepository(session)
        ticket = await repository.lock_ticket(ticket_id)
        if ticket is None:
            return None

        validate_transition(ticket.status, new_status)
        old_status = ticket.status
        ticket.status = new_status
        add_ticket_message(
            session,
            ticket,
            MessageAuthorType.SYSTEM,
            f"Статус изменён: {old_status.value} -> {new_status.value} (администратор {admin_username})",
        )
        session.add(
            Log(
                user_id=ticket.user_id,
                action="web_status_change",
                details=(
                    f"Администратор {admin_username} изменил статус заявки "
                    f"#{ticket.id}: {old_status.value} -> {new_status.value}"
                ),
            )
        )

        scheduled = False
        if not ticket.is_anonymous and ticket.user and ticket.user.vk_id:
            local_num = await repository.local_number(ticket.user_id, ticket.id)
            add_outbox_message(
                session,
                ticket.user.vk_id,
                f"Статус вашей заявки #{local_num} изменён: {status_label(ticket.status)}",
            )
            scheduled = True

    if scheduled:
        (delivery_callback or fire_outbox_delivery)()
    return ticket


async def assign_ticket_department(
    ticket_id: int,
    department_id: int,
    admin_username: str,
) -> Ticket | None:
    """Переназначить заявку отделу с журналированием операции."""
    async with ticket_transaction() as session:
        repository = TicketRepository(session)
        ticket = await repository.lock_ticket(ticket_id)
        if ticket is None:
            return None
        department = await repository.get_department(department_id)
        if department is None:
            return None

        old_department = ticket.department.name if ticket.department else "—"
        ticket.department_id = department_id
        add_ticket_message(
            session,
            ticket,
            MessageAuthorType.SYSTEM,
            f"Заявка передана из отдела «{old_department}» в отдел «{department.name}» "
            f"(администратор {admin_username})",
        )
        session.add(
            Log(
                user_id=ticket.user_id,
                action="web_assign",
                details=(
                    f"Администратор {admin_username} передал заявку #{ticket.id} "
                    f"из «{old_department}» в «{department.name}»"
                ),
            )
        )
        return ticket
