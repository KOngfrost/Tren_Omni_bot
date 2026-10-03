"""
Маршруты дашборда.

Безопасность:
- IDOR: админ видит только статистику своего отдела (суперадмин — все)
"""

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from core.admin_presence import ADMIN_PRESENCE_TTL_SECONDS, count_online
from core.database import async_session_maker
from core.models import Department, Ticket, TicketStatus
from core.time_utils import day_start_app_tz
from web.dependencies import get_admin_scope, require_auth
from web.security.csrf import get_csrf_token
from web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
async def dashboard(request: Request, user: dict = Depends(require_auth)):
    """Главная страница дашборда с IDOR-защитой."""
    db_error: bool = False
    recent_tickets: list[Ticket] = []
    ticket_counts: dict[TicketStatus, int] = {}
    unassigned_total: int = 0
    unassigned_new: int = 0
    completed_today: int = 0
    departments_count: int = 0
    online_admins: int = 0

    try:
        async with async_session_maker() as session:
            # Область видимости: суперадмин — все отделы,
            # админ отдела — только свой отдел
            is_super, dept_id = await get_admin_scope(session, user)

            scope = [] if is_super else [Ticket.department_id == dept_id]

            # Все статусы одним запросом: SELECT status, COUNT(*) GROUP BY status
            rows = await session.execute(
                select(Ticket.status, func.count(Ticket.id)).where(*scope).group_by(Ticket.status)
            )
            ticket_counts = {status: count for status, count in rows.all() if status is not None}

            # Обращения без отдела («Общие вопросы») не должны теряться в общей
            # куче: считаем их ОТДЕЛЬНО, одним запросом с группировкой статуса.
            unassigned_rows = await session.execute(
                select(Ticket.status, func.count(Ticket.id))
                .where(Ticket.department_id.is_(None))
                .group_by(Ticket.status)
            )
            unassigned_by_status = {
                status: count for status, count in unassigned_rows.all() if status is not None
            }
            unassigned_total = sum(unassigned_by_status.values())
            unassigned_new = unassigned_by_status.get(TicketStatus.NEW, 0)

            # Решено сегодня — для верхней метрики на дашборде.
            completed_today = int(
                (
                    await session.scalar(
                        select(func.count(Ticket.id))
                        .where(*scope)
                        .where(
                            Ticket.status.in_(
                                [TicketStatus.COMPLETED, TicketStatus.COMPLETED_AUTO]
                            )
                        )
                        .where(Ticket.updated_at >= day_start_app_tz())
                    )
                )
                or 0
            )

            departments_count = int(await session.scalar(select(func.count(Department.id))) or 0)

            # Последние заявки
            recent_stmt = (
                select(Ticket)
                .options(selectinload(Ticket.user), selectinload(Ticket.department))
                .order_by(Ticket.created_at.desc())
                .limit(10)
            )
            if not is_super:
                if dept_id is not None:
                    recent_stmt = recent_stmt.where(
                        or_(Ticket.department_id == dept_id, Ticket.department_id.is_(None))
                    )
                else:
                    recent_stmt = recent_stmt.where(Ticket.department_id.is_(None))
            recent_tickets = list((await session.execute(recent_stmt)).scalars().all())
    except Exception as e:
        logger.error("Не удалось загрузить статистику: %s", e)
        db_error = True

    # Присутствие админов — для «живого» статуса в шапке. Сбой Redis не должен
    # ломать страницу, поэтому ошибка гасится.
    try:
        online_admins = await count_online()
    except Exception as exc:  # pragma: no cover - зависит от состояния Redis
        logger.debug("Не удалось получить число администраторов онлайн: %s", exc)

    completed_statuses = [TicketStatus.COMPLETED, TicketStatus.COMPLETED_AUTO]
    stats = {
        "total_tickets": sum(ticket_counts.values()),
        "in_progress": ticket_counts.get(TicketStatus.IN_PROGRESS, 0),
        "completed": sum(ticket_counts.get(s, 0) for s in completed_statuses),
        "new_tickets": ticket_counts.get(TicketStatus.NEW, 0),
        "completed_today": completed_today,
    }

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "stats": stats,
            "unassigned_total": unassigned_total,
            "unassigned_new": unassigned_new,
            "departments_count": departments_count,
            "online_admins": online_admins,
            "presence_window": ADMIN_PRESENCE_TTL_SECONDS,
            "recent_tickets": recent_tickets,
            "db_error": db_error,
            "active": "dashboard",
            "csrf_token": get_csrf_token(request),
            "session_id": request.state.session_id,
        },
    )
