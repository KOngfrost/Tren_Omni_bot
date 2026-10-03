"""Форматирование заявок для интерфейсов VK и веб-панели."""

from core.models import MessageAuthorType, Ticket, TicketMessage, TicketStatus


def status_label(status: TicketStatus | str | None) -> str:
    """Русская метка статуса (безопасно для любого входа)."""
    if status is None:
        return "—"
    if isinstance(status, TicketStatus):
        return status.value
    return status


def format_ticket_list(
    tickets: list[Ticket], user_ticket_map: dict[int, int] | None = None
) -> str:
    """Список заявок для VK-сообщения.

    ``user_ticket_map`` задаёт персональные номера заявок, если передан.
    """
    if not tickets:
        return "У вас пока нет заявок."

    lines = [f"Ваши заявки (последние {len(tickets)}):", ""]
    for ticket in tickets:
        created = ticket.created_at.strftime("%d.%m.%Y") if ticket.created_at else "—"
        dept = ticket.department.name if ticket.department else "—"
        display_number = ticket.id
        if ticket.id is not None and user_ticket_map:
            display_number = user_ticket_map.get(ticket.id, ticket.id)
        lines.append(f"#{display_number} · {dept} · {ticket.topic or 'Без темы'}")
        lines.append(f"   Статус: {status_label(ticket.status)} · создана {created}")
        if ticket.description:
            preview = " ".join(ticket.description.split())
            lines.append(f"   Вопрос: {preview[:160]}{'...' if len(preview) > 160 else ''}")
        if ticket.response_text:
            preview = ticket.response_text[:120]
            lines.append(f"   Ответ: {preview}{'…' if len(ticket.response_text) > 120 else ''}")
        lines.append("")
    lines.append("Нажмите «Подробнее #N», чтобы увидеть историю заявки.")
    return "\n".join(lines)


def format_ticket_details(
    ticket: Ticket,
    messages: list[TicketMessage],
    local_id: int | None = None,
) -> str:
    """Подробная карточка заявки с историей переписки."""
    created = ticket.created_at.strftime("%d.%m.%Y %H:%M") if ticket.created_at else "—"
    dept = ticket.department.name if ticket.department else "—"
    display_id = local_id if local_id is not None else ticket.id

    lines = [
        f"Заявка #{display_id}",
        f"Отдел: {dept}",
        f"Тема: {ticket.topic or 'Без темы'}",
        f"Статус: {status_label(ticket.status)}",
        f"Создана: {created}",
        "",
        "История обращений:",
        "— — —",
    ]

    if not messages:
        lines.append(f"Вы: {ticket.description or '—'}")
        if ticket.response_text:
            lines.append(f"Администратор: {ticket.response_text}")
    else:
        for message in messages:
            if message.author_type is None:
                continue
            author = {
                MessageAuthorType.USER: "Вы",
                MessageAuthorType.ADMIN: "Администратор",
                MessageAuthorType.SYSTEM: "Система",
            }.get(message.author_type, "—")
            when = message.created_at.strftime("%d.%m.%Y %H:%M") if message.created_at else ""
            lines.append(f"[{when}] {author}: {message.message}")

    return "\n".join(lines)


def mask_anonymous_data(full_name: str | None, is_anonymous: bool) -> str:
    """Маскировка персональных данных анонимных заявок."""
    if is_anonymous:
        return "Аноним"
    return full_name or "—"
