"""Операции создания заявки и ответов студента."""

from collections.abc import Callable

from core.formatters.ticket_formatter import mask_anonymous_data
from core.models import Log, MessageAuthorType, Ticket, TicketStatus, User
from core.outbox import add_outbox_message, fire_outbox_delivery
from core.repositories.ticket_repo import TicketRepository
from core.services.ticket_primitives import (
    COMPLETED_STATUSES,
    add_ticket_message,
    ticket_transaction,
)
from core.services.ticket_routing_service import _resolve_department


async def create_ticket(
    topic: str,
    description: str,
    vk_id: int | None = None,
    keep_identity: bool = False,
    department_name: str | None = None,
    *,
    delivery_callback: Callable[[], None] | None = None,
) -> Ticket:
    """Создать обращение студента и поставить уведомления в outbox."""
    async with ticket_transaction() as session:
        repository = TicketRepository(session)
        user = None
        if keep_identity and vk_id is not None:
            user = await repository.user_by_vk_id(vk_id)
            if user is None:
                user = User(vk_id=vk_id)
                repository.add(user)
                await repository.flush()

        department = await _resolve_department(session, department_name)
        ticket = Ticket(
            user_id=user.id if user else None,
            department=department,
            topic=topic,
            description=description,
            status=TicketStatus.NEW,
            is_anonymous=not keep_identity,
            auto_closed=False,
        )
        repository.add(ticket)
        await repository.flush()
        add_ticket_message(
            session,
            ticket,
            MessageAuthorType.USER,
            description,
            author_vk_id=vk_id if keep_identity else None,
        )
        repository.add(
            Log(
                user_id=user.id if user else None,
                action="ticket_created",
                details=f"Создана заявка #{ticket.id}: {topic}",
            )
        )

        scheduled = False
        student_info = mask_anonymous_data(user.full_name if user else None, ticket.is_anonymous)
        dorm_info = (
            f" ({user.dormitory})" if user and user.dormitory and not ticket.is_anonymous else ""
        )

        if department and department.id:
            department_admins = await repository.admins_for_department(department.id)
            superadmins = await repository.superadmins()
            recipients = {admin.id: admin for admin in (*department_admins, *superadmins)}
        else:
            recipients = {admin.id: admin for admin in await repository.assigned_admins()}

        for admin in recipients.values():
            if not admin.user or not admin.user.vk_id:
                continue
            if department and department.id:
                notification = (
                    f"Новая заявка #{ticket.id} [{department.name}]\n"
                    f"От: {student_info}{dorm_info}\n"
                    f"Тема: {topic}\n\nТекст: {description}\n\n"
                    f"Для работы напишите: «Заявка #{ticket.id}»"
                )
            else:
                notification = (
                    f"Новое общее обращение #{ticket.id} [Общий вопрос]\n"
                    f"От: {student_info}{dorm_info}\n"
                    f"Тема: {topic}\n\nТекст: {description}\n\n"
                    "Обращение не закреплено за отделом — ответьте первым "
                    "или передайте его в профильный отдел.\n"
                    f"Для работы напишите: «Заявка #{ticket.id}»"
                )
            add_outbox_message(session, admin.user.vk_id, notification)
            scheduled = True

    if scheduled:
        (delivery_callback or fire_outbox_delivery)()
    return ticket


async def add_student_reply(
    ticket_id: int,
    vk_id: int,
    message: str,
    *,
    delivery_callback: Callable[[], None] | None = None,
) -> Ticket | None:
    """Добавить ответ студента в его заявку и уведомить ответственных."""
    async with ticket_transaction() as session:
        repository = TicketRepository(session)
        ticket = await repository.lock_user_ticket(ticket_id, vk_id)
        if ticket is None or ticket.is_anonymous:
            return None

        add_ticket_message(session, ticket, MessageAuthorType.USER, message, author_vk_id=vk_id)
        old_status = ticket.status
        reopened = old_status in COMPLETED_STATUSES
        ticket.status = TicketStatus.NEW
        add_ticket_message(
            session,
            ticket,
            MessageAuthorType.SYSTEM,
            f"Студент направил уточняющий вопрос/ответ: статус возвращён в «Новое» "
            f"для рассмотрения администратором (был «{old_status.value}»)",
        )
        repository.add(
            Log(
                user_id=ticket.user_id,
                action="student_reply",
                details=f"Студент направил ответ в заявку #{ticket.id} (статус переведён в «Новое»)",
            )
        )

        admins = await repository.admins_for_ticket(ticket.department_id)
        scheduled = False
        for admin in admins:
            if admin.user and admin.user.vk_id:
                subject = "Повторно открыто" if reopened else "Новый ответ студента"
                add_outbox_message(
                    session,
                    admin.user.vk_id,
                    f"{subject}\n\nСтудент ответил на заявку #{ticket.id}:\n\n{message}",
                )
                scheduled = True

    if scheduled:
        (delivery_callback or fire_outbox_delivery)()
    return ticket
