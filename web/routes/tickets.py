"""
Маршруты заявок веб-панели.

Безопасность:
- CSRF защищён middleware CSRFMiddleware в main.py
- IDOR: админ отдела видит и изменяет только заявки своего отдела
  (суперадмин и VIEWER — все, VIEWER только на чтение)
- Изменения статусов валидируются по схеме переходов (ticket_service)
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from core.database import async_session_maker
from core.models import Ticket, TicketStatus
from core.ticket_service import (
    StatusTransitionError,
    assign_ticket_department,
    change_ticket_status,
    get_ticket_messages,
    reply_to_ticket,
)
from web.constants import STATUS_CHOICES
from web.dependencies import (
    get_admin_scope,
    get_departments_for_user,
    require_auth,
    require_superadmin,
    require_writer,
)
from web.routes.auth import require_crud_rate_limit
from web.security.csrf import get_csrf_token
from web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
async def tickets_page(
    request: Request,
    page: int = 1,
    page_size: int = 25,
    q: str = "",
    status: str = "",
    department_id: int | None = None,
    user: dict = Depends(require_auth),
):
    """Страница заявок с IDOR-защитой и пагинацией."""
    tickets: list[Ticket] = []
    departments: list = []
    db_error: bool = False
    total: int = 0
    current_page: int = max(1, page)
    page_size = min(max(1, page_size), 100)
    offset: int = (current_page - 1) * page_size
    total_pages: int = 1

    try:
        async with async_session_maker() as session:
            is_super, dept_id = await get_admin_scope(session, user)

            count_stmt = select(func.count(Ticket.id))
            filters: list = []
            if not is_super:
                filters.append(Ticket.department_id == dept_id)
            elif department_id is not None:
                filters.append(Ticket.department_id == department_id)
            if q.strip():
                pattern: str = f"%{q.strip()}%"
                filters.append(or_(Ticket.topic.ilike(pattern), Ticket.description.ilike(pattern)))
            if status:
                try:
                    filters.append(Ticket.status == TicketStatus(status))
                except ValueError:
                    status = ""
            count_stmt = count_stmt.where(*filters)
            total = int((await session.scalar(count_stmt)) or 0)

            stmt = (
                select(Ticket)
                .options(selectinload(Ticket.user), selectinload(Ticket.department))
                .order_by(Ticket.created_at.desc())
                .offset(offset)
                .limit(page_size)
            )
            stmt = stmt.where(*filters)
            tickets = list((await session.scalars(stmt)).all())

            departments = await get_departments_for_user(session, user)
    except Exception as e:
        db_error = True
        logger.error("Не удалось загрузить заявки: %s", e)

    total_pages = max(1, (total + page_size - 1) // page_size if total > 0 else 1)

    return templates.TemplateResponse(
        "tickets.html",
        {
            "request": request,
            "user": user,
            "tickets": tickets,
            "departments": departments,
            "status_choices": STATUS_CHOICES,
            "db_error": db_error,
            "active": "tickets",
            "flash_success": request.session.pop("flash_success", None),
            "flash_error": request.session.pop("flash_error", None),
            "csrf_token": get_csrf_token(request),
            "current_page": current_page,
            "total_pages": total_pages,
            "total_tickets": total,
            "page_size": page_size,
            "query": q,
            "selected_status": status,
            "selected_department": department_id,
            "session_id": request.state.session_id,
        },
    )


async def _load_ticket_for_user(ticket_id: int, user: dict) -> Ticket:
    """Загрузить заявку с проверкой IDOR-прав (404/403)."""
    async with async_session_maker() as session:
        is_super, dept_id = await get_admin_scope(session, user)

    ticket = await _get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    if not is_super and ticket.department_id != dept_id:
        raise HTTPException(status_code=403, detail="Нет прав для работы с этой заявкой")
    return ticket


async def _get_ticket(ticket_id: int) -> Ticket | None:
    async with async_session_maker() as session:
        return await session.scalar(
            select(Ticket)
            .options(selectinload(Ticket.user), selectinload(Ticket.department))
            .where(Ticket.id == ticket_id)
        )


@router.get("/{ticket_id}")
async def get_ticket(ticket_id: int, user: dict = Depends(require_auth)):
    """Данные заявки для модального окна (JSON) с проверкой прав + история."""
    ticket = await _load_ticket_for_user(ticket_id, user)

    try:
        messages: list = await get_ticket_messages(ticket_id)
    except Exception:
        messages = []

    return {
        "id": ticket.id,
        "topic": ticket.topic,
        "description": ticket.description,
        "status": {"value": ticket.status.value if ticket.status else ""},
        "is_anonymous": ticket.is_anonymous,
        "auto_closed": ticket.auto_closed,
        "response_text": ticket.response_text,
        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
        "user": {
            "full_name": ticket.user.full_name if ticket.user else None,
            "dormitory": ticket.user.dormitory if ticket.user else None,
        } if ticket.user else None,
        "department": {
            "name": ticket.department.name if ticket.department else None,
        } if ticket.department else None,
        "messages": [
            {
                "author_type": m.author_type.value if m.author_type else None,
                "message": m.message,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
    }


@router.post("/{ticket_id}/reply")
async def reply_ticket(ticket_id: int, request: Request, user: dict = Depends(require_writer)):
    """Ответ администратора студенту.

    Форма: message (обязательно), complete=on (завершить заявку).
    Сохраняет сообщение, обновляет response_text, статус, шлёт VK-уведомление.

    Безопасность:
    - CSRF: защищён middleware CSRFMiddleware
    - Rate limiting: не более 20 запросов на IP за 5 минут
    - IDOR: проверка department_id через БД
    """
    require_crud_rate_limit(request)
    form = await request.form()
    message: str = str(form.get("message", "")).strip()
    complete: bool = form.get("complete") == "on"

    if not message:
        raise HTTPException(status_code=400, detail="Текст ответа не может быть пустым")

    await _load_ticket_for_user(ticket_id, user)

    ticket, vk_sent = await reply_to_ticket(
        ticket_id=ticket_id,
        admin_username=user.get("username", "unknown"),
        message=message,
        complete=complete,
    )
    if ticket is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    note: str = "" if vk_sent else " (VK-уведомление не доставлено)"
    request.session["flash_success"] = f"Ответ на заявку #{ticket_id} отправлен{note}"
    return RedirectResponse(url="/tickets/", status_code=303)


@router.post("/{ticket_id}/status")
async def set_ticket_status(ticket_id: int, request: Request, user: dict = Depends(require_writer)):
    """Смена статуса заявки с валидацией переходов.

    Безопасность:
    - CSRF: защищён middleware CSRFMiddleware
    - Rate limiting: не более 20 запросов на IP за 5 минут
    - IDOR: проверка department_id через БД
    - Валидация переходов статусов через ticket_service
    """
    require_crud_rate_limit(request)
    form = await request.form()
    new_status_raw: str = str(form.get("status", ""))

    try:
        new_status = TicketStatus(new_status_raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Неизвестный статус") from None

    await _load_ticket_for_user(ticket_id, user)

    try:
        ticket = await change_ticket_status(
            ticket_id=ticket_id,
            new_status=new_status,
            admin_username=user.get("username", "unknown"),
        )
    except StatusTransitionError as error:
        request.session["flash_error"] = str(error)
        return RedirectResponse(url="/tickets/", status_code=303)

    if ticket is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    request.session["flash_success"] = f"Статус заявки #{ticket_id}: {new_status.value}"
    return RedirectResponse(url="/tickets/", status_code=303)


@router.post("/{ticket_id}/assign")
async def assign_ticket(ticket_id: int, request: Request, user: dict = Depends(require_superadmin)):
    """Передача заявки другому отделу (только суперадмин).

    Безопасность:
    - CSRF: защищён middleware CSRFMiddleware
    - Rate limiting: не более 20 запросов на IP за 5 минут
    - Только суперадмин может передавать заявки
    - IDOR: проверка department_id через БД
    """
    require_crud_rate_limit(request)
    form = await request.form()
    try:
        department_id: int = int(str(form.get("department_id", 0)))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректный отдел") from None

    await _load_ticket_for_user(ticket_id, user)

    ticket = await assign_ticket_department(
        ticket_id=ticket_id,
        department_id=department_id,
        admin_username=user.get("username", "unknown"),
    )
    if ticket is None:
        raise HTTPException(status_code=404, detail="Заявка или отдел не найдены")

    request.session["flash_success"] = f"Заявка #{ticket_id} передана в отдел"
    return RedirectResponse(url="/tickets/", status_code=303)
