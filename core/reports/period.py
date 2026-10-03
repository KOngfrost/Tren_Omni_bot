"""Извлечение данных отчётности и идемпотентность запусков."""

import logging
import os
from datetime import date, datetime, timedelta

from sqlalchemy import and_, select

from core.database import async_session_maker
from core.models import Department, ReportRun, Ticket, User
from core.reports.daily import _DeptRef, _TicketRow, _UserRef

logger = logging.getLogger(__name__)
REPORT_CHUNK_SIZE = int(os.environ.get("REPORT_CHUNK_SIZE", "1000"))


def _ticket_from_record(row) -> _TicketRow:
    """Строка потоковой выборки -> облегчённая заявка для Excel."""
    return _TicketRow(
        id=row.id,
        created_at=row.created_at,
        topic=row.topic,
        description=row.description,
        status=row.status,
        response_text=row.response_text,
        auto_closed=bool(row.auto_closed),
        is_anonymous=bool(row.is_anonymous),
        department_id=row.department_id,
        department=_DeptRef(row.department_id, row.name) if row.department_id else None,
        user=_UserRef(row.full_name, row.dormitory) if row.full_name or row.dormitory else None,
    )


async def _fetch_report_data(
    period_start: datetime,
    period_end: datetime,
    *,
    end_inclusive: bool,
) -> dict:
    """Пакетно прочитать заявки и отделы в пределах временного окна."""
    if end_inclusive:
        period_filter = and_(Ticket.created_at >= period_start, Ticket.created_at <= period_end)
    else:
        period_filter = and_(Ticket.created_at >= period_start, Ticket.created_at < period_end)

    statement = (
        select(
            Ticket.id,
            Ticket.created_at,
            Ticket.topic,
            Ticket.description,
            Ticket.status,
            Ticket.response_text,
            Ticket.auto_closed,
            Ticket.is_anonymous,
            Ticket.department_id,
            Department.name,
            User.full_name,
            User.dormitory,
        )
        .outerjoin(Department, Department.id == Ticket.department_id)
        .outerjoin(User, User.id == Ticket.user_id)
        .where(period_filter)
        .order_by(Ticket.created_at, Ticket.id)
    )

    async with async_session_maker() as session:
        departments = list(
            (await session.scalars(select(Department).order_by(Department.name))).all()
        )
        result = await session.execute(statement)
        tickets = [_ticket_from_record(row) for row in result.all()]

    if len(tickets) >= REPORT_CHUNK_SIZE:
        logger.info("Отчёт: выбрано %s заявок", len(tickets))
    return {"tickets": tickets, "departments": departments}


async def get_report_for_date(report_day: date) -> dict:
    """Собрать данные для отчёта за одну дату."""
    from core.reports.daily import get_app_tz

    timezone = get_app_tz()
    day_start = datetime.combine(report_day, datetime.min.time(), tzinfo=timezone)
    day_end = day_start + timedelta(days=1)
    return await _fetch_report_data(day_start, day_end, end_inclusive=False)


async def get_report_for_period(date_from: date, date_to: date) -> dict:
    """Собрать данные для отчёта за период включительно."""
    if date_from > date_to:
        raise ValueError("Дата начала не может быть позже даты окончания")

    from core.reports.daily import get_app_tz

    timezone = get_app_tz()
    period_start = datetime.combine(date_from, datetime.min.time(), tzinfo=timezone)
    period_end = datetime.combine(date_to, datetime.max.time(), tzinfo=timezone)
    return await _fetch_report_data(period_start, period_end, end_inclusive=True)


async def is_report_already_sent(report_day: date) -> bool:
    """Проверить Redis и БД на наличие успешно зафиксированного отчёта."""
    try:
        from core.redis_client import get_redis_client

        redis = await get_redis_client()
        if redis and await redis.get(f"oss_bot:report_sent:{report_day}"):
            return True
    except Exception:
        pass

    try:
        async with async_session_maker() as session:
            existing = await session.scalar(
                select(ReportRun).where(ReportRun.report_date == report_day)
            )
            if existing is None:
                return False
            try:
                from core.redis_client import get_redis_client

                redis = await get_redis_client()
                if redis:
                    await redis.set(f"oss_bot:report_sent:{report_day}", "1", ex=86400 * 2)
            except Exception:
                pass
            return True
    except Exception as exc:
        logger.warning("Не удалось проверить статус отправки отчёта в БД: %s", exc)
    return False


async def mark_report_sent(report_day: date, status: str = "sent") -> None:
    """Зафиксировать отправку отчёта в Redis и БД идемпотентно."""
    try:
        from core.redis_client import get_redis_client

        redis = await get_redis_client()
        if redis:
            await redis.set(f"oss_bot:report_sent:{report_day}", "1", ex=86400 * 2)
    except Exception as exc:
        logger.warning("Не удалось записать статус отчёта в Redis: %s", exc)

    try:
        async with async_session_maker() as session:
            existing = await session.scalar(
                select(ReportRun).where(ReportRun.report_date == report_day)
            )
            if existing is not None:
                return
            session.add(ReportRun(report_date=report_day, status=status))
            await session.commit()
    except Exception as exc:
        logger.error("Не удалось зафиксировать ReportRun в БД: %s", exc)


__all__ = [
    "REPORT_CHUNK_SIZE",
    "_fetch_report_data",
    "get_report_for_date",
    "get_report_for_period",
    "is_report_already_sent",
    "mark_report_sent",
]
