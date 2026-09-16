"""
Маршруты событий.

Безопасность:
- CSRF защищён middleware CSRFMiddleware в main.py
- IDOR: админ видит только события своего отдела (суперадмин — все)
- Санитизация входных данных от XSS
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.database import async_session_maker
from core.models import Department, Event
from web.dependencies import (
    get_admin_scope,
    get_departments_for_user,
    require_auth,
    require_writer,
)
from web.form_utils import parse_form_int
from web.security.csrf import get_csrf_token
from web.security.middleware import sanitize_html
from web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
async def events_page(request: Request, user=Depends(require_auth)):
    """Страница событий с IDOR-защитой."""
    events: list[Event] = []
    departments: list[Department] = []
    db_error: bool = False

    try:
        async with async_session_maker() as session:
            is_super, dept_id = await get_admin_scope(session, user)
            departments = await get_departments_for_user(session, user)

            events_stmt = (
                select(Event)
                .options(selectinload(Event.department), selectinload(Event.registrations))
                .order_by(Event.event_date)
            )
            if not is_super:
                events_stmt = events_stmt.where(
                    (Event.department_id == dept_id) | (Event.department_id.is_(None))
                )
            events = list((await session.execute(events_stmt)).scalars().all())
    except Exception as e:
        db_error = True
        logger.error("Не удалось загрузить события: %s", e)

    return templates.TemplateResponse(
        "events.html",
        {
            "request": request,
            "user": user,
            "events": events,
            "departments": departments,
            "db_error": db_error,
            "active": "events",
            "flash_success": request.session.pop("flash_success", None),
            "flash_error": request.session.pop("flash_error", None),
            "csrf_token": get_csrf_token(request),
            "session_id": request.state.session_id,
        },
    )


@router.post("/")
async def add_event(request: Request, user=Depends(require_writer)):
    """Создание события с санитизацией входных данных."""
    form = await request.form()
    title: str = sanitize_html(str(form.get("title", "")))
    description: str = sanitize_html(str(form.get("description", "")))
    event_date_str: str = str(form.get("event_date", ""))

    if not title.strip():
        request.session["flash_error"] = "Название мероприятия обязательно"
        return RedirectResponse(url="/events/", status_code=303)

    try:
        event_date = datetime.fromisoformat(event_date_str)
        if event_date.tzinfo is None:
            event_date = event_date.replace(tzinfo=UTC)
    except ValueError:
        request.session["flash_error"] = "Некорректная дата мероприятия"
        return RedirectResponse(url="/events/", status_code=303)

    try:
        async with async_session_maker() as session:
            is_super, dept_id = await get_admin_scope(session, user)
            department_id: int | None = parse_form_int(form, "department_id", default=dept_id)

            if not is_super:
                # Админ отдела жёстко привязан к своему отделу
                department_id = dept_id
            if department_id is None:
                request.session["flash_error"] = "Не выбран отдел"
                return RedirectResponse(url="/events/", status_code=303)

            session.add(
                Event(
                    department_id=department_id,
                    title=title,
                    description=description or None,
                    event_date=event_date,
                )
            )
            await session.commit()
    except Exception:
        logger.exception("Не удалось создать мероприятие")
        request.session["flash_error"] = "Не удалось сохранить мероприятие. Попробуйте позже."
        return RedirectResponse(url="/events/", status_code=303)

    request.session["flash_success"] = "Мероприятие создано"
    return RedirectResponse(url="/events/", status_code=303)


@router.post("/{event_id}/delete")
async def delete_event(request: Request, event_id: int, user=Depends(require_writer)):
    """Удаление мероприятия (POST с CSRF-токеном) с проверкой прав."""
    try:
        async with async_session_maker() as session:
            event = await session.get(Event, event_id)
            if not event:
                request.session["flash_error"] = "Мероприятие не найдено"
                return RedirectResponse(url="/events/", status_code=303)

            # IDOR: админ отдела может удалять только события своего отдела
            is_super, dept_id = await get_admin_scope(session, user)
            if not is_super and event.department_id != dept_id:
                request.session["flash_error"] = "Нет прав для удаления этого мероприятия"
                return RedirectResponse(url="/events/", status_code=303)

            await session.delete(event)
            await session.commit()
    except Exception:
        logger.exception("Не удалось удалить мероприятие")
        request.session["flash_error"] = "Не удалось удалить мероприятие. Попробуйте позже."
        return RedirectResponse(url="/events/", status_code=303)

    request.session["flash_success"] = "Мероприятие удалено"
    return RedirectResponse(url="/events/", status_code=303)
