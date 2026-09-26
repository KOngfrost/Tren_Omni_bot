"""
Маршруты заявок веб-панели.

Безопасность:
- CSRF защищён middleware CSRFMiddleware в main.py
- IDOR: админ отдела видит и изменяет только заявки своего отдела
  (суперадмин — все заявки)
- Изменения статусов валидируются по схеме переходов (ticket_service)
"""

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import selectinload

from core.database import async_session_maker
from core.models import Department, Ticket, User
from core.ticket_service import (
    StatusTransitionError,
    assign_ticket_department,
    change_ticket_status,
    get_ticket_messages,
    reply_to_ticket,
)
from web.constants import STATUS_CHOICES, TICKET_FILTER_CHOICES, resolve_ticket_status
from web.dependencies import (
    get_admin_scope,
    get_departments_for_user,
    require_auth,
    require_superadmin,
    require_writer,
)
from web.routes.auth import require_crud_rate_limit
from web.security.csrf import get_csrf_token
from web.templating import format_datetime, templates

logger = logging.getLogger(__name__)

router = APIRouter()


def _escape_like(text: str) -> str:
    """Экранировать спецсимволы ILIKE (_, %, \\) для предотвращения wildcard injection."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_ticket_search_filter(clean_q: str):
    """Полнотекстовый гибкий поиск по словам, фразе, номеру, автору и отделу.

    Использует единую форму проверки регистра — ILIKE (case-insensitive),
    которая поддерживается GIN-индексами pg_trgm (см. миграцию c4d5e6f7a8b9).
    """
    terms = [t for t in clean_q.split() if t]
    term_conditions = []
    for term in terms:
        term_clean_num = term.lstrip("#").lstrip("№").strip()
        esc_term = _escape_like(term)
        conds: list[Any] = [
            Ticket.topic.ilike(f"%{esc_term}%", escape="\\"),
            Ticket.description.ilike(f"%{esc_term}%", escape="\\"),
            Ticket.response_text.ilike(f"%{esc_term}%", escape="\\"),
            User.full_name.ilike(f"%{esc_term}%", escape="\\"),
            cast(User.vk_id, String).like(f"%{esc_term}%", escape="\\"),
            Department.name.ilike(f"%{esc_term}%", escape="\\"),
        ]
        if term_clean_num.isdigit():
            conds.append(Ticket.id == int(term_clean_num))
        term_conditions.append(or_(*conds))

    if len(terms) > 1:
        phrase_clean_num = clean_q.lstrip("#").lstrip("№").strip()
        esc_phrase = _escape_like(clean_q)
        phrase_conds: list[Any] = [
            Ticket.topic.ilike(f"%{esc_phrase}%", escape="\\"),
            Ticket.description.ilike(f"%{esc_phrase}%", escape="\\"),
            Ticket.response_text.ilike(f"%{esc_phrase}%", escape="\\"),
            User.full_name.ilike(f"%{esc_phrase}%", escape="\\"),
            Department.name.ilike(f"%{esc_phrase}%", escape="\\"),
        ]
        if phrase_clean_num.isdigit():
            phrase_conds.append(Ticket.id == int(phrase_clean_num))
        return or_(and_(*term_conditions), or_(*phrase_conds))
    if term_conditions:
        return term_conditions[0]
    return None


@router.get("/")
async def tickets_page(
    request: Request,
    page: str | int = 1,
    page_size: str | int = 25,
    q: str = "",
    status: str = "",
    department_id: str | None = None,
    user: dict = Depends(require_auth),
):
    """Страница заявок с IDOR-защитой, поиском и пагинацией."""
    tickets: list[Ticket] = []
    departments: list = []
    db_error: bool = False
    total: int = 0

    # Безопасный парсинг числовых параметров (защита от 422 при пустых строках в HTML-форме)
    try:
        current_page = max(1, int(page))
    except (ValueError, TypeError):
        current_page = 1

    try:
        clean_page_size = min(max(1, int(page_size)), 100)
    except (ValueError, TypeError):
        clean_page_size = 25

    offset: int = (current_page - 1) * clean_page_size
    total_pages: int = 1

    dept_filter_id: int | None = None
    if department_id is not None and str(department_id).strip().isdigit():
        dept_filter_id = int(str(department_id).strip())

    resolved_status = resolve_ticket_status(status)

    try:
        async with async_session_maker() as session:
            is_super, dept_id = await get_admin_scope(session, user)

            count_stmt = (
                select(func.count(Ticket.id))
                .outerjoin(User, Ticket.user_id == User.id)
                .outerjoin(Department, Ticket.department_id == Department.id)
            )
            filters: list = []

            # IDOR разграничение
            if not is_super:
                if dept_id is None:
                    filters.append(Ticket.id == -1)
                else:
                    filters.append(Ticket.department_id == dept_id)
            elif dept_filter_id is not None:
                filters.append(Ticket.department_id == dept_filter_id)

            # Фильтр по статусу
            if resolved_status is not None:
                filters.append(Ticket.status == resolved_status)

            # Полнотекстовый гибкий поиск
            clean_q = q.strip()
            if clean_q:
                search_filter = _build_ticket_search_filter(clean_q)
                if search_filter is not None:
                    filters.append(search_filter)

            count_stmt = count_stmt.where(*filters)
            total = int((await session.scalar(count_stmt)) or 0)

            stmt = (
                select(Ticket)
                .outerjoin(User, Ticket.user_id == User.id)
                .outerjoin(Department, Ticket.department_id == Department.id)
                .options(selectinload(Ticket.user), selectinload(Ticket.department))
                .order_by(Ticket.created_at.desc())
                .offset(offset)
                .limit(clean_page_size)
            )
            stmt = stmt.where(*filters)
            tickets = list((await session.scalars(stmt)).all())

            departments = await get_departments_for_user(session, user)
    except Exception as e:
        db_error = True
        logger.error("Не удалось загрузить заявки: %s", e)

    total_pages = max(1, (total + clean_page_size - 1) // clean_page_size if total > 0 else 1)

    return templates.TemplateResponse(
        "tickets.html",
        {
            "request": request,
            "user": user,
            "tickets": tickets,
            "departments": departments,
            "status_choices": STATUS_CHOICES,
            "filter_status_choices": TICKET_FILTER_CHOICES,
            "db_error": db_error,
            "active": "tickets",
            "flash_success": request.session.pop("flash_success", None),
            "flash_error": request.session.pop("flash_error", None),
            "csrf_token": get_csrf_token(request),
            "current_page": current_page,
            "total_pages": total_pages,
            "total_tickets": total,
            "page_size": clean_page_size,
            "query": q,
            "selected_status": resolved_status.value if resolved_status else "",
            "selected_department": dept_filter_id,
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
    if not is_super and (dept_id is None or ticket.department_id != dept_id):
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
        "status": {
            "value": (
                ticket.status.value
                if hasattr(ticket.status, "value")
                else (str(ticket.status) if ticket.status else "")
            )
        },
        "is_anonymous": ticket.is_anonymous,
        "auto_closed": ticket.auto_closed,
        "response_text": ticket.response_text,
        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
        # Ошибка #19: дата, отрендеренная сервером в едином поясе проекта
        # (APP_TIMEZONE, Europe/Moscow) — клиент использует её вместо
        # локального toLocaleString() браузера.
        "created_at_display": (
            format_datetime(ticket.created_at, "%d.%m.%Y %H:%M") + " МСК"
            if ticket.created_at
            else None
        ),
        "user": {
            "full_name": ticket.user.full_name if ticket.user else None,
            "dormitory": ticket.user.dormitory if ticket.user else None,
        }
        if ticket.user
        else None,
        "department": {
            "name": ticket.department.name if ticket.department else None,
        }
        if ticket.department
        else None,
        "messages": [
            {
                "author_type": (
                    m.author_type.value
                    if hasattr(m.author_type, "value")
                    else (str(m.author_type) if m.author_type else None)
                ),
                "message": m.message,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "created_at_display": (
                    format_datetime(m.created_at, "%d.%m.%Y %H:%M") + " МСК"
                    if m.created_at
                    else None
                ),
            }
            for m in messages
        ],
    }


@router.post("/{ticket_id}/reply")
async def reply_ticket(ticket_id: int, request: Request, user: dict = Depends(require_writer)):
    """Ответ администратора студенту.

    Поддерживает как классическую отправку HTML-формы, так и AJAX-запрос.
    Форма: message (обязательно), complete (завершить заявку).
    Сохраняет сообщение, обновляет response_text, статус, шлёт VK-уведомление.

    Безопасность:
    - CSRF: защищён middleware CSRFMiddleware
    - Rate limiting: не более 20 запросов на IP за 5 минут
    - IDOR: проверка department_id через БД
    """
    await require_crud_rate_limit(request)

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        message = str(body.get("message", "")).strip()
        complete = bool(body.get("complete", False))
    else:
        form = await request.form()
        message = str(form.get("message", "")).strip()
        complete = form.get("complete") in ("on", "true", True, "1")

    if not message:
        raise HTTPException(status_code=400, detail="Текст ответа не может быть пустым")

    await _load_ticket_for_user(ticket_id, user)

    admin_username = user.get("username", "unknown")
    ticket, vk_sent = await reply_to_ticket(
        ticket_id=ticket_id,
        admin_username=admin_username,
        message=message,
        complete=complete,
    )
    if ticket is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    is_ajax = (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in request.headers.get("accept", "")
        or "application/json" in content_type
    )

    if is_ajax:
        from core.time_utils import get_app_tz
        now_dt = datetime.now(get_app_tz())
        return {
            "success": True,
            "ticket_id": ticket_id,
            "status": ticket.status.value if hasattr(ticket.status, "value") else str(ticket.status),
            "vk_sent": vk_sent,
            "message": {
                "author_type": "ADMIN",
                "message": message,
                "created_at": now_dt.isoformat(),
                "created_at_display": format_datetime(now_dt, "%d.%m.%Y %H:%M") + " МСК",
            },
        }

    note: str = "" if vk_sent else " (VK-уведомление не доставлено)"
    request.session["flash_success"] = f"Ответ на заявку #{ticket_id} отправлен{note}"
    return RedirectResponse(url="/tickets/", status_code=303)


@router.post("/{ticket_id}/status")
async def set_ticket_status(
    ticket_id: int, request: Request, user: dict = Depends(require_writer)
):
    """Смена статуса заявки с валидацией переходов.

    Безопасность:
    - CSRF: защищён middleware CSRFMiddleware
    - Rate limiting: не более 20 запросов на IP за 5 минут
    - IDOR: проверка department_id через БД
    - Валидация переходов статусов через ticket_service
    """
    await require_crud_rate_limit(request)
    form = await request.form()
    new_status_raw: str = str(form.get("status", ""))

    new_status = resolve_ticket_status(new_status_raw)
    if new_status is None:
        raise HTTPException(status_code=400, detail="Неизвестный статус")

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
async def assign_ticket(
    ticket_id: int, request: Request, user: dict = Depends(require_superadmin)
):
    """Передача заявки другому отделу (только суперадмин).

    Безопасность:
    - CSRF: защищён middleware CSRFMiddleware
    - Rate limiting: не более 20 запросов на IP за 5 минут
    - Только суперадмин может передавать заявки
    - IDOR: проверка department_id через БД
    """
    await require_crud_rate_limit(request)
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
