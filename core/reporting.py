import asyncio
import logging
import secrets
from datetime import UTC, date, datetime, timedelta
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO
from zoneinfo import ZoneInfo

import aiosmtplib
from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from vkbottle.tools.uploader import DocMessagesUploader

from core.config import settings
from core.database import async_session_maker
from core.models import Admin, Department, ReportRun, Ticket, TicketStatus, User, UserRole
from core.ticket_service import COMPLETED_STATUSES, status_label

logger = logging.getLogger(__name__)


def get_app_tz() -> ZoneInfo:
    """Часовой пояс приложения (APP_TIMEZONE, по умолчанию Europe/Moscow)."""
    return ZoneInfo(settings.APP_TIMEZONE)


def _build_summary_sheet(workbook: Workbook, data: dict) -> None:
    """Лист 1: Сводка по отделам и процент выполнения."""
    sheet = workbook.active
    sheet.title = "Сводка"

    headers = [
        "Отдел",
        "Новые",
        "В обработке",
        "Передано в адм.",
        "Передано в хоз.",
        "Выполнено",
        "Выполнено (авто)",
        "Анонимные",
        "Всего",
        "% выполнения",
    ]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    tickets = data["tickets"]
    departments = data["departments"]

    def _count(predicate) -> int:
        return sum(1 for t in tickets if predicate(t))

    rows = []
    for department in departments:
        dept_tickets = [t for t in tickets if t.department_id == department.id]
        if not dept_tickets:
            continue
        total = len(dept_tickets)
        completed = sum(1 for t in dept_tickets if t.status in COMPLETED_STATUSES)
        percent = round(completed / total * 100, 1) if total else 0.0
        rows.append(
            [
                department.name,
                _count(
                    lambda t, d=department: (
                        t.department_id == d.id and t.status == TicketStatus.NEW
                    )
                ),
                _count(
                    lambda t, d=department: (
                        t.department_id == d.id and t.status == TicketStatus.IN_PROGRESS
                    )
                ),
                _count(
                    lambda t, d=department: (
                        t.department_id == d.id and t.status == TicketStatus.TRANSFERRED_ADMIN
                    )
                ),
                _count(
                    lambda t, d=department: (
                        t.department_id == d.id
                        and t.status == TicketStatus.TRANSFERRED_HOUSEKEEPING
                    )
                ),
                _count(
                    lambda t, d=department: (
                        t.department_id == d.id and t.status == TicketStatus.COMPLETED
                    )
                ),
                _count(
                    lambda t, d=department: (
                        t.department_id == d.id and t.status == TicketStatus.COMPLETED_AUTO
                    )
                ),
                _count(
                    lambda t, d=department: (
                        t.department_id == d.id and t.status == TicketStatus.ANONYMOUS
                    )
                ),
                total,
                percent,
            ]
        )

    # Итоговая строка
    total = len(tickets)
    completed = sum(1 for t in tickets if t.status in COMPLETED_STATUSES)
    percent = round(completed / total * 100, 1) if total else 0.0
    rows.append(
        [
            "ИТОГО",
            _count(lambda t: t.status == TicketStatus.NEW),
            _count(lambda t: t.status == TicketStatus.IN_PROGRESS),
            _count(lambda t: t.status == TicketStatus.TRANSFERRED_ADMIN),
            _count(lambda t: t.status == TicketStatus.TRANSFERRED_HOUSEKEEPING),
            _count(lambda t: t.status == TicketStatus.COMPLETED),
            _count(lambda t: t.status == TicketStatus.COMPLETED_AUTO),
            _count(lambda t: t.status == TicketStatus.ANONYMOUS),
            total,
            percent,
        ]
    )

    for row in rows:
        sheet.append(row)

    for column in sheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column)
        sheet.column_dimensions[column[0].column_letter].width = max_length + 2


def _build_details_sheet(workbook: Workbook, data: dict) -> None:
    """Лист 2: Детализация заявок."""
    sheet = workbook.create_sheet("Детализация")

    headers = [
        "ID",
        "ФИО",
        "Общежитие",
        "Отдел",
        "Тема",
        "Описание",
        "Статус",
        "Ответ",
        "Маркер",
    ]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for ticket in data["tickets"]:
        marker = "авто" if ticket.auto_closed else "ручной"
        # Маскируем персональные данные для анонимных заявок
        if ticket.is_anonymous:
            full_name = "Аноним"
            dormitory = "Аноним"
        else:
            full_name = ticket.user.full_name if ticket.user and ticket.user.full_name else "—"
            dormitory = ticket.user.dormitory if ticket.user and ticket.user.dormitory else "—"
        sheet.append(
            [
                ticket.id,
                full_name,
                dormitory,
                ticket.department.name if ticket.department else "—",
                ticket.topic or "",
                ticket.description or "",
                status_label(ticket.status),
                ticket.response_text or "",
                marker,
            ]
        )

    for column in sheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column)
        sheet.column_dimensions[column[0].column_letter].width = min(max_length + 2, 60)


def _build_anonymous_sheet(workbook: Workbook, data: dict) -> None:
    """Лист 3: Анонимные обращения."""
    sheet = workbook.create_sheet("Анонимные обращения")

    headers = ["ID", "Тема", "Описание", "Статус", "Дата создания"]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    anonymous = [
        t for t in data["tickets"] if t.is_anonymous or t.status == TicketStatus.ANONYMOUS
    ]
    for ticket in anonymous:
        sheet.append(
            [
                ticket.id,
                ticket.topic or "",
                ticket.description or "",
                status_label(ticket.status),
                ticket.created_at.strftime("%Y-%m-%d %H:%M") if ticket.created_at else "",
            ]
        )

    for column in sheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column)
        sheet.column_dimensions[column[0].column_letter].width = min(max_length + 2, 60)


def build_daily_report(data: dict, report_date: datetime) -> bytes:
    """Строит Excel-отчёт по ТЗ: Сводка, Детализация, Анонимные обращения."""
    workbook = Workbook()

    _build_summary_sheet(workbook, data)
    _build_details_sheet(workbook, data)
    _build_anonymous_sheet(workbook, data)

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


async def send_report_email(report_bytes: bytes, filename: str) -> None:
    """Отправляет отчёт по email через SMTP (асинхронно)."""
    if not settings.SMTP_HOST or not settings.REPORT_EMAILS:
        logger.info("SMTP не настроен, email-рассылка пропущена")
        return

    msg = MIMEMultipart()
    msg["Subject"] = f"Отчёт студенческого бота за {datetime.now(UTC):%d.%m.%Y}"
    msg["From"] = settings.SMTP_FROM
    msg["To"] = ", ".join(settings.REPORT_EMAILS)

    msg.attach(MIMEText("Ежедневный отчёт во вложении.", "plain", "utf-8"))

    attachment = MIMEApplication(
        report_bytes,
        _subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    attachment.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(attachment)

    await aiosmtplib.send(
        msg,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        start_tls=True,
        username=settings.SMTP_USER,
        password=settings.SMTP_PASSWORD,
        timeout=30,
    )

    logger.info("Отчёт отправлен по email: %s", ", ".join(settings.REPORT_EMAILS))


async def send_report_to_vk(api, admin_vk_id: int, report_bytes: bytes, filename: str) -> None:
    """Отправляет отчёт в VK как документ через встроенный uploader vkbottle."""
    if not admin_vk_id:
        raise ValueError("VK_REPORT_ADMIN_ID не настроен")

    uploader = DocMessagesUploader(api)
    attachment = await uploader.upload(
        file_source=report_bytes,
        peer_id=admin_vk_id,
        title=filename,
    )

    await api.messages.send(
        peer_id=admin_vk_id,
        random_id=secrets.randbelow(2**31) + 1,
        message="Ежедневный отчёт во вложении.",
        attachment=attachment,
    )


async def get_superadmin_vk_ids() -> list[int]:
    """Возвращает VK ID всех суперадминистраторов с доступным VK ID."""
    async with async_session_maker() as session:
        result = await session.scalars(
            select(User.vk_id)
            .join(Admin, Admin.user_id == User.id)
            .where(Admin.role == UserRole.SUPERADMIN, User.vk_id.is_not(None))
            .distinct()
        )
        return [vk_id for vk_id in result if vk_id is not None]


def _seconds_until_report() -> float:
    """Секунды до ближайшего запуска отчёта (в часовом поясе APP_TIMEZONE)."""
    try:
        report_time = datetime.strptime(settings.REPORT_TIME, "%H:%M").time()
    except ValueError as error:
        raise ValueError("REPORT_TIME должен быть в формате HH:MM") from error

    tz = get_app_tz()
    now = datetime.now(tz)
    next_report = datetime.combine(now.date(), report_time, tzinfo=tz)
    if next_report <= now:
        next_report += timedelta(days=1)
    return (next_report - now).total_seconds()


def parse_report_date(text: str) -> date | None:
    """Парсит дату из строки формата ДД.ММ.ГГГГ или ДД.ММ.ГГ.

    Возвращает date или None, если формат неверный.
    """
    text = text.strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


async def get_report_for_date(report_day: date) -> dict:
    """Собирает данные для отчёта за одну конкретную дату (асинхронная обёртка)."""
    tz = get_app_tz()
    day_start = datetime.combine(report_day, datetime.min.time(), tzinfo=tz)
    day_end = day_start + timedelta(days=1)

    async with async_session_maker() as session:
        tickets = await session.scalars(
            select(Ticket)
            .options(selectinload(Ticket.user), selectinload(Ticket.department))
            .where(Ticket.created_at >= day_start, Ticket.created_at < day_end)
            .order_by(Ticket.created_at)
        )
        departments = await session.scalars(select(Department).order_by(Department.name))
    return {"tickets": list(tickets), "departments": list(departments)}


async def get_report_for_period(date_from: date, date_to: date) -> dict:
    """Собирает данные для отчёта за период (от date_from до date_to включительно)."""
    if date_from > date_to:
        raise ValueError("Дата начала не может быть позже даты окончания")

    tz = get_app_tz()
    period_start = datetime.combine(date_from, datetime.min.time(), tzinfo=tz)
    period_end = datetime.combine(date_to, datetime.max.time(), tzinfo=tz)

    async with async_session_maker() as session:
        tickets = await session.scalars(
            select(Ticket)
            .options(selectinload(Ticket.user), selectinload(Ticket.department))
            .where(Ticket.created_at >= period_start, Ticket.created_at <= period_end)
            .order_by(Ticket.created_at)
        )
        departments = await session.scalars(select(Department).order_by(Department.name))
    return {"tickets": list(tickets), "departments": list(departments)}


async def is_report_already_sent(report_day: date) -> bool:
    """Был ли отчёт за указанную дату уже отправлен (таблица report_runs)."""
    async with async_session_maker() as session:
        existing = await session.scalar(
            select(ReportRun).where(ReportRun.report_date == report_day)
        )
        return existing is not None


async def mark_report_sent(report_day: date, status: str = "sent") -> None:
    """Зафиксировать факт отправки отчёта за дату (защита от дублей, идемпотентно)."""
    async with async_session_maker() as session:
        existing = await session.scalar(
            select(ReportRun).where(ReportRun.report_date == report_day)
        )
        if existing is not None:
            return
        session.add(ReportRun(report_date=report_day, status=status))
        await session.commit()


async def _run_report(api, admin_vk_ids: list[int], report_date: datetime) -> None:
    """Сформировать и отправить ежедневный отчет всем суперадминам и по email."""
    report_day = report_date.date()

    if await is_report_already_sent(report_day):
        logger.info("Отчёт за %s уже отправлен ранее — пропуск", report_day)
        return

    data = await get_report_for_date(report_date.date())
    report_bytes = build_daily_report(data, report_date)
    filename = f"report_{report_date:%Y-%m-%d}.xlsx"

    # Отправка в VK — отдельный try/except
    vk_failed = False
    email_configured = bool(settings.SMTP_HOST and settings.REPORT_EMAILS)
    delivery_configured = bool(admin_vk_ids or email_configured)
    if not delivery_configured:
        logger.error("Отчёт не отправлен: не настроен ни один канал доставки")
        return

    for admin_vk_id in admin_vk_ids:
        try:
            await send_report_to_vk(api, admin_vk_id, report_bytes, filename)
        except Exception:
            logger.exception("Не удалось отправить отчёт в VK администратору %s", admin_vk_id)
            vk_failed = True

    # Отправка по email — отдельный try/except
    email_failed = False
    if email_configured:
        try:
            await send_report_email(report_bytes, filename)
        except Exception:
            logger.exception("Не удалось отправить отчёт по email")
            email_failed = True

    if not vk_failed and not email_failed:
        await mark_report_sent(report_day)
        logger.info("Ежедневный отчёт за %s отправлен", report_day)


async def _report_loop(api) -> None:
    tz = get_app_tz()
    while True:
        try:
            await asyncio.sleep(_seconds_until_report())

            # Отчёт за вчера по часовому поясу приложения
            report_date = datetime.now(tz) - timedelta(days=1)
            await _run_report(api, await get_superadmin_vk_ids(), report_date)
        except Exception:
            logger.exception("Не удалось отправить ежедневный отчёт")
            await asyncio.sleep(60)


def start_report_scheduler(api) -> asyncio.Task:
    """Запускает asyncio-планировщик ежедневных отчётов."""
    task = asyncio.create_task(_report_loop(api))
    return task
