"""Общие константы веб-панели.

Здесь живут значения, которые используются более чем одним маршрутом,
чтобы избежать дублирования (единый источник правды).
"""

from core.models import TicketStatus

# Человекочитаемые названия статусов для форм смены статуса заявки.
# Используется в tickets.py и dept_frame.py.
# Включает полный перечень допустимых статусов: модальное окно тикета
# сопоставляет текущий статус с пунктами списка и выставляет selected
# строго для текущего статуса (предотвращение сброса в первый пункт).
STATUS_CHOICES = [
    (TicketStatus.NEW.value, "Новое"),
    (TicketStatus.IN_PROGRESS.value, "В обработке"),
    (TicketStatus.TRANSFERRED_ADMIN.value, "Передать в администрацию"),
    (TicketStatus.TRANSFERRED_HOUSEKEEPING.value, "Передать в локальный Студсовет"),
    (TicketStatus.COMPLETED.value, "Выполнено"),
    (TicketStatus.COMPLETED_AUTO.value, "Выполнено (авто)"),
]

# Полный список статусов для фильтрации в реестре заявок
TICKET_FILTER_CHOICES = [
    (TicketStatus.NEW.value, "Новые"),
    (TicketStatus.IN_PROGRESS.value, "В обработке"),
    (TicketStatus.TRANSFERRED_ADMIN.value, "В администрации "),
    (TicketStatus.TRANSFERRED_HOUSEKEEPING.value, "В локальном Студсовете"),
    (TicketStatus.COMPLETED.value, "Выполнено"),
    (TicketStatus.COMPLETED_AUTO.value, "Выполнено (авто)"),
    (TicketStatus.ANONYMOUS.value, "Анонимные"),
]


def resolve_ticket_status(val: str | None) -> TicketStatus | None:
    """Универсальное разрешение статуса заявки из любых входных данных."""
    if not val or not str(val).strip():
        return None
    cleaned = str(val).strip()
    # 1. По точному значению (на русском: "В обработке", "Новое", "Выполнено" и т.п.)
    try:
        return TicketStatus(cleaned)
    except ValueError:
        pass
    # 2. По имени enum (на англ: "IN_PROGRESS", "NEW", "COMPLETED_AUTO", "ANONYMOUS")
    try:
        return TicketStatus[cleaned.upper()]
    except KeyError:
        pass
    # 3. Регистронезависимо по имени или значению
    for member in TicketStatus:
        if member.name.lower() == cleaned.lower() or member.value.lower() == cleaned.lower():
            return member
    return None


# CSS-класс бейджа для каждого статуса заявки (фильтр status_badge).
STATUS_BADGE_CLASS = {
    TicketStatus.NEW: "badge-new",
    TicketStatus.IN_PROGRESS: "badge-in-progress",
    TicketStatus.COMPLETED: "badge-completed",
    TicketStatus.COMPLETED_AUTO: "badge-completed",
    TicketStatus.TRANSFERRED_ADMIN: "badge-in-progress",
    TicketStatus.TRANSFERRED_HOUSEKEEPING: "badge-in-progress",
    TicketStatus.ANONYMOUS: "badge-anonymous",
}


def status_badge_class(status: TicketStatus | str | None) -> str:
    """CSS-класс бейджа по статусу заявки (fallback — нейтральный)."""
    if isinstance(status, str):
        try:
            status = TicketStatus(status)
        except ValueError:
            return "badge-anonymous"
    if status is None:
        return "badge-anonymous"
    return STATUS_BADGE_CLASS.get(status) or "badge-anonymous"
