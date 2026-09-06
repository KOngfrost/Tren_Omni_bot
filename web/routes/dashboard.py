"""
Маршруты дашборда.

Безопасность:
- IDOR: админ видит только статистику своего отдела (суперадмин — все)
"""

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from core.database import async_session_maker
from core.models import Ticket, TicketStatus
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

    try:
        async with async_session_maker() as session:
            # Область видимости: суперадмин/VIEWER — все отделы,
            # админ отдела — только свой отдел
            is_super, dept_id = await get_admin_scope(session, user)

            scope = [] if is_super else [Ticket.department_id == dept_id]

            # Все статусы одним запросом: SELECT status, COUNT(*) GROUP BY status
            rows = await session.execute(
                select(Ticket.status, func.count(Ticket.id))
                .where(*scope)
                .group_by(Ticket.status)
            )
            ticket_counts = dict(rows.all())

            # Последние заявки
            recent_stmt = (
                select(Ticket)
                .options(selectinload(Ticket.user), selectinload(Ticket.department))
                .order_by(Ticket.created_at.desc())
                .limit(10)
            )
            if not is_super:
                recent_stmt = recent_stmt.where(Ticket.department_id == dept_id)
            recent_tickets = list((await session.execute(recent_stmt)).scalars().all())
    except Exception as e:
        logger.error("Не удалось загрузить статистику: %s", e)
        db_error = True

    completed_statuses = [TicketStatus.COMPLETED, TicketStatus.COMPLETED_AUTO]
    stats = {
        "total_tickets": sum(ticket_counts.values()),
        "in_progress": ticket_counts.get(TicketStatus.IN_PROGRESS, 0),
        "completed": sum(ticket_counts.get(s, 0) for s in completed_statuses),
        "new_tickets": ticket_counts.get(TicketStatus.NEW, 0),
    }

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "stats": stats,
            "recent_tickets": recent_tickets,
            "db_error": db_error,
            "active": "dashboard",
            "csrf_token": get_csrf_token(request),
            "session_id": request.state.session_id,
        },
    )
