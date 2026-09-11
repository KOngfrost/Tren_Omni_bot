"""
Маршруты логов.

Безопасность:
- Пагинация: логи загружаются страницами по 100 записей (DoS protection)
- CSV-экспорт: санитизация полей от CSV-injection
- IDOR: суперадмин видит все логи, обычный админ — логи своего отдела
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from core.database import async_session_maker
from core.models import Admin, Log, Ticket
from web.dependencies import get_admin_scope, require_auth
from web.security.csrf import get_csrf_token
from web.security.middleware import escape_for_csv, sanitize_csv_field
from web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()

LOGS_PER_PAGE = 100  # Пагинация: 100 записей на страницу


@router.get("/")
async def logs_page(
    request: Request,
    page: str | int = 1,
    user=Depends(require_auth),
):
    """Страница логов с пагинацией."""
    logs: list[Log] = []
    db_error: bool = False
    total: int = 0
    try:
        current_page = max(1, int(page))
    except (ValueError, TypeError):
        current_page = 1
    offset: int = (current_page - 1) * LOGS_PER_PAGE

    try:
        async with async_session_maker() as session:
            is_super, dept_id = await get_admin_scope(session, user)

            logs_stmt = (
                select(Log)
                .options(selectinload(Log.user))
                .order_by(Log.created_at.desc())
                .offset(offset)
                .limit(LOGS_PER_PAGE)
            )
            if not is_super:
                admin_user_ids = select(Admin.user_id).where(
                    Admin.department_id == dept_id, Admin.user_id.is_not(None)
                )
                ticket_user_ids = select(Ticket.user_id).where(
                    Ticket.department_id == dept_id, Ticket.user_id.is_not(None)
                )
                dept_filter = or_(Log.user_id.in_(admin_user_ids), Log.user_id.in_(ticket_user_ids))
                logs_stmt = logs_stmt.where(dept_filter)
                count_stmt = select(func.count(Log.id)).where(dept_filter)
            else:
                count_stmt = select(func.count(Log.id))
            logs_result = await session.execute(logs_stmt)
            logs = list(logs_result.scalars().all())
            total = int((await session.scalar(count_stmt)) or 0)
    except Exception as e:
        db_error = True
        logger.error("Не удалось загрузить логи: %s", e)

    total_pages = (total + LOGS_PER_PAGE - 1) // LOGS_PER_PAGE if total > 0 else 1

    return templates.TemplateResponse(
        "logs.html",
        {
            "request": request,
            "user": user,
            "logs": logs,
            "db_error": db_error,
            "active": "logs",
            "current_page": current_page,
            "total_pages": total_pages,
            "total_logs": total,
            "csrf_token": get_csrf_token(request),
            "session_id": request.state.session_id,
        },
    )


@router.get("/export")
async def export_logs(user=Depends(require_auth)):
    """Экспорт логов в CSV с защитой от CSV-injection.

    Безопасность:
    - Все поля экранируются через escape_for_csv и sanitize_csv_field
    - Лимит: максимум 10000 записей за раз (DoS protection)
    """
    try:
        async with async_session_maker() as session:
            is_super, dept_id = await get_admin_scope(session, user)
            logs_stmt = (
                select(Log)
                .options(selectinload(Log.user))
                .order_by(Log.created_at.desc())
                .limit(10000)  # Лимит для экспорта
            )
            if not is_super:
                admin_user_ids = select(Admin.user_id).where(
                    Admin.department_id == dept_id, Admin.user_id.is_not(None)
                )
                ticket_user_ids = select(Ticket.user_id).where(
                    Ticket.department_id == dept_id, Ticket.user_id.is_not(None)
                )
                dept_filter = or_(Log.user_id.in_(admin_user_ids), Log.user_id.in_(ticket_user_ids))
                logs_stmt = logs_stmt.where(dept_filter)
            logs_result = await session.execute(logs_stmt)
            logs: list[Log] = list(logs_result.scalars().all())
    except Exception:
        logs = []

    csv_content: str = "\ufeffID,Пользователь,Действие,Детали,Дата\n"
    for log in logs:
        username: str = escape_for_csv((log.user.full_name or "Аноним") if log.user else "Аноним")
        action: str = escape_for_csv(log.action or "")
        details: str = escape_for_csv(sanitize_csv_field(log.details or ""))
        date_str: str = log.created_at.strftime("%Y-%m-%d %H:%M") if log.created_at else ""
        csv_content += f"{log.id},{username},{action},{details},{date_str}\n"

    return Response(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=logs_{datetime.now().strftime('%Y-%m-%d')}.csv"
        },
    )
