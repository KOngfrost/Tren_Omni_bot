"""Общие константы веб-панели.

Здесь живут значения, которые используются более чем одним маршрутом,
чтобы избежать дублирования (единый источник правды).
"""

from core.models import TicketStatus

# Человекочитаемые названия статусов для форм смены статуса заявки.
# Используется в tickets.py и dept_frame.py.
STATUS_CHOICES = [
    (TicketStatus.IN_PROGRESS.value, "В обработке"),
    (TicketStatus.TRANSFERRED_ADMIN.value, "Передать в администрацию СГ"),
    (TicketStatus.TRANSFERRED_HOUSEKEEPING.value, "Передать в Хозчасть"),
    (TicketStatus.COMPLETED.value, "Выполнено"),
]

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
