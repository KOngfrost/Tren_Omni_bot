"""Подготовка ежедневного Excel-отчёта."""

from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Font

from core.config import settings
from core.models import Department, TicketStatus
from core.ticket_service import COMPLETED_STATUSES, status_label

DEFAULT_DEPTS = ["Жилищно-бытовой", "Информационный", "Корпоративный", "Культурно-массовый"]


@dataclass(frozen=True, slots=True)
class _DeptRef:
    """Облегчённая ссылка на отдел вместо ORM-объекта Department."""

    id: int | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class _UserRef:
    """Облегчённая ссылка на пользователя вместо ORM-объекта User."""

    full_name: str | None = None
    dormitory: str | None = None


@dataclass(frozen=True, slots=True)
class _TicketRow:
    """Заявка в виде, достаточном для Excel-отчёта."""

    id: int
    created_at: datetime | None = None
    topic: str | None = None
    description: str | None = None
    status: TicketStatus = TicketStatus.NEW
    response_text: str | None = None
    auto_closed: bool = False
    is_anonymous: bool = False
    department_id: int | None = None
    department: _DeptRef | None = None
    user: _UserRef | None = None


def get_app_tz() -> ZoneInfo:
    """Часовой пояс приложения (APP_TIMEZONE, по умолчанию Europe/Moscow)."""
    return ZoneInfo(settings.APP_TIMEZONE)


def _get_report_departments(data: dict) -> list[Department]:
    """Вернуть все отделы из данных или dev-fallback для пустой БД."""
    departments = list(data.get("departments", []))
    if departments:
        return departments
    return [Department(id=-(index + 1), name=name) for index, name in enumerate(DEFAULT_DEPTS)]


def _build_summary_sheet(workbook: Workbook, data: dict) -> None:
    """Сформировать лист сводной аналитики по отделам и статусам."""
    sheet = workbook.active
    sheet.title = "Краткая информация"
    sheet.append(
        [
            "Отдел",
            "Новые",
            "В обработке",
            "Передано в адм.",
            "Передано в локальный Студсовет",
            "Выполнено",
            "Выполнено (авто)",
            "Анонимные",
            "Всего",
            "% выполнения",
        ]
    )
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    tickets = data.get("tickets", [])
    departments = _get_report_departments(data)

    def _count(predicate) -> int:
        return sum(1 for ticket in tickets if predicate(ticket))

    rows = []
    for department in departments:
        department_tickets = [
            ticket
            for ticket in tickets
            if getattr(ticket, "department_id", None) == getattr(department, "id", None)
            or getattr(getattr(ticket, "department", None), "name", None) == department.name
        ]
        total = len(department_tickets)
        completed = sum(1 for ticket in department_tickets if ticket.status in COMPLETED_STATUSES)
        percent = round(completed / total * 100, 1) if total else 0.0

        def _count_dept(predicate, tickets_list=department_tickets) -> int:
            return sum(1 for ticket in tickets_list if predicate(ticket))

        rows.append(
            [
                department.name,
                _count_dept(lambda ticket: ticket.status == TicketStatus.NEW),
                _count_dept(lambda ticket: ticket.status == TicketStatus.IN_PROGRESS),
                _count_dept(lambda ticket: ticket.status == TicketStatus.TRANSFERRED_ADMIN),
                _count_dept(lambda ticket: ticket.status == TicketStatus.TRANSFERRED_HOUSEKEEPING),
                _count_dept(lambda ticket: ticket.status == TicketStatus.COMPLETED),
                _count_dept(lambda ticket: ticket.status == TicketStatus.COMPLETED_AUTO),
                _count_dept(lambda ticket: ticket.status == TicketStatus.ANONYMOUS),
                total,
                percent,
            ]
        )

    total = len(tickets)
    completed = sum(1 for ticket in tickets if ticket.status in COMPLETED_STATUSES)
    percent = round(completed / total * 100, 1) if total else 0.0
    rows.append(
        [
            "ИТОГО",
            _count(lambda ticket: ticket.status == TicketStatus.NEW),
            _count(lambda ticket: ticket.status == TicketStatus.IN_PROGRESS),
            _count(lambda ticket: ticket.status == TicketStatus.TRANSFERRED_ADMIN),
            _count(lambda ticket: ticket.status == TicketStatus.TRANSFERRED_HOUSEKEEPING),
            _count(lambda ticket: ticket.status == TicketStatus.COMPLETED),
            _count(lambda ticket: ticket.status == TicketStatus.COMPLETED_AUTO),
            _count(lambda ticket: ticket.status == TicketStatus.ANONYMOUS),
            total,
            percent,
        ]
    )

    for row in rows:
        sheet.append(row)
    for column in sheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column)
        sheet.column_dimensions[column[0].column_letter].width = max_length + 2


def _sanitize_excel_cell(value: Any) -> Any:
    """Защитить Excel от formula injection в строковых значениях."""
    if not isinstance(value, str):
        return value
    stripped = value.lstrip()
    if stripped and stripped[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{value}"
    return value


def _build_department_sheets(workbook: Workbook, data: dict) -> None:
    """Сформировать подробный реестр заявок для каждого отдела."""
    tickets = data.get("tickets", [])
    departments = _get_report_departments(data)
    headers = ["ID", "Дата", "ФИО", "Общежитие", "Тема", "Описание", "Статус", "Ответ", "Маркер"]
    used_titles = {"Краткая информация"}
    for department in departments:
        base_title = (department.name or f"Отдел {department.id}")[:31]
        title = base_title
        counter = 1
        while title in used_titles:
            title = f"{base_title[:28]}_{counter}"
            counter += 1
        used_titles.add(title)

        sheet = workbook.create_sheet(title)
        sheet.append(headers)
        for cell in sheet[1]:
            cell.font = Font(bold=True)

        department_tickets = [
            ticket
            for ticket in tickets
            if getattr(ticket, "department_id", None) == getattr(department, "id", None)
            or getattr(getattr(ticket, "department", None), "name", None) == department.name
        ]
        for ticket in department_tickets:
            marker = "авто" if getattr(ticket, "auto_closed", False) else "ручной"
            if getattr(ticket, "is_anonymous", False):
                full_name = dormitory = "Аноним"
            else:
                user = getattr(ticket, "user", None)
                full_name = user.full_name if user and user.full_name else "—"
                dormitory = user.dormitory if user and user.dormitory else "—"

            created_at = getattr(ticket, "created_at", None)
            if created_at:
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
                created_text = created_at.astimezone(get_app_tz()).strftime("%Y-%m-%d %H:%M")
            else:
                created_text = ""

            sheet.append(
                [
                    ticket.id,
                    created_text,
                    _sanitize_excel_cell(full_name),
                    _sanitize_excel_cell(dormitory),
                    _sanitize_excel_cell(ticket.topic or ""),
                    _sanitize_excel_cell(ticket.description or ""),
                    status_label(ticket.status),
                    _sanitize_excel_cell(ticket.response_text or ""),
                    marker,
                ]
            )

        for column in sheet.columns:
            max_length = max(len(str(cell.value or "")) for cell in column)
            sheet.column_dimensions[column[0].column_letter].width = min(max_length + 2, 50)


def build_daily_report(data: dict, report_date: datetime) -> bytes:
    """Сформировать ежедневный Excel-отчёт в памяти."""
    workbook = Workbook()
    _build_summary_sheet(workbook, data)
    _build_department_sheets(workbook, data)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
