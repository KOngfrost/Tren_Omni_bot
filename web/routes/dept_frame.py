"""
Фреймы отделов: изолированная панель каждого отдела.

Доступ (IDOR-защита):
- SUPERADMIN и VIEWER: любой отдел (VIEWER — только чтение);
- DEPARTMENT_ADMIN: только свой отдел, department_id подтверждается
  через БД (web_users/admins), а не по данным сессии.

Содержимое фрейма: статистика заявок, последние заявки, база знаний,
FAQ и события отдела — только реальные данные из БД.
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from core.database import async_session_maker
from core.models import Department, Event, FAQNode, KnowledgeBase, Ticket, TicketStatus
from web.dependencies import can_write, get_admin_scope, get_departments_for_user, require_auth
from web.security.csrf import get_csrf_token
from web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()


async def _load_dept_context(session, dept_id: int, user: dict):
    """Загрузить отдел и его данные; None, если отдела нет или нет прав."""
    is_super, own_dept_id = await get_admin_scope(session, user)

    dept = await session.get(Department, dept_id)
    if dept is None:
        return None
    # Обычный админ имеет доступ только к своему отделу
    if not is_super and own_dept_id != dept_id:
        return None

    def _count(*conditions):
        return select(func.count(Ticket.id)).where(Ticket.department_id == dept_id, *conditions)

    stats = {
        "total_tickets": await session.scalar(_count()) or 0,
        "in_progress": await session.scalar(
            _count(Ticket.status == TicketStatus.IN_PROGRESS)
        ) or 0,
        "completed": await session.scalar(
            _count(Ticket.status.in_([TicketStatus.COMPLETED, TicketStatus.COMPLETED_AUTO]))
        ) or 0,
        "new_tickets": await session.scalar(
            _count(Ticket.status == TicketStatus.NEW)
        ) or 0,
    }

    recent_tickets = list((await session.execute(
        select(Ticket)
        .options(selectinload(Ticket.user), selectinload(Ticket.department))
        .where(Ticket.department_id == dept_id)
        .order_by(Ticket.created_at.desc())
        .limit(10)
    )).scalars().all())

    kb_items = list((await session.execute(
        select(KnowledgeBase)
        .where(KnowledgeBase.department_id == dept_id)
        .order_by(KnowledgeBase.id)
    )).scalars().all())

    faq_items = list((await session.execute(
        select(FAQNode)
        .where(FAQNode.department_id == dept_id)
        .order_by(FAQNode.order_index, FAQNode.id)
    )).scalars().all())

    events = list((await session.execute(
        select(Event)
        .options(selectinload(Event.registrations))
        .where(Event.department_id == dept_id)
        .order_by(Event.event_date)
    )).scalars().all())

    return {
        "dept": dept,
        "stats": stats,
        "recent_tickets": recent_tickets,
        "kb_items": kb_items,
        "faq_items": faq_items,
        "events": events,
        "can_write": can_write(user),
    }


@router.get("/{dept_id}/")
async def dept_frame(request: Request, dept_id: int, user=Depends(require_auth)):
    """Фрейм отдела — изолированная панель управления отделом."""
    try:
        async with async_session_maker() as session:
            context = await _load_dept_context(session, dept_id, user)

            if context is None:
                request.session["error"] = "Отдел не найден или нет прав доступа"
                return RedirectResponse(url="/", status_code=303)

            # Отделы для навигации/переключателя (суперадмин — все)
            all_depts = await get_departments_for_user(session, user)
            csrf_token = get_csrf_token(request)
    except Exception as e:
        logger.error("Не удалось загрузить фрейм отдела %s: %s", dept_id, e)
        context = None
        all_depts = []
        csrf_token = get_csrf_token(request)

    if context is None:
        return templates.TemplateResponse(
            "dept_frame.html",
            {
                "request": request,
                "user": user,
                "department": None,
                "departments": all_depts,
                "db_error": True,
                "active": "dept",
                "csrf_token": csrf_token,
                "error": request.session.pop("error", None),
                "session_id": request.state.session_id,
            },
        )

    return templates.TemplateResponse(
        "dept_frame.html",
        {
            "request": request,
            "user": user,
            "department": context["dept"],
            "stats": context["stats"],
            "recent_tickets": context["recent_tickets"],
            "kb_items": context["kb_items"],
            "faq_items": context["faq_items"],
            "events": context["events"],
            "can_write": context["can_write"],
            "departments": all_depts,
            "db_error": False,
            "active": "dept",
            "csrf_token": csrf_token,
            "success": request.session.pop("success", None),
            "error": request.session.pop("error", None),
            "session_id": request.state.session_id,
        },
    )
