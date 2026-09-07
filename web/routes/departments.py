"""
Маршруты управления отделами.

Безопасность:
- CSRF защищён middleware CSRFMiddleware в main.py
- Все операции — только для суперадмина (require_superadmin)
- Rate limiting: не более 20 операций на IP за 5 минут
- Удаление отдела запрещено, пока с ним связаны данные (заявки, БЗ, FAQ,
  события, веб-пользователи) — защита от случайной потери контента
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from core.database import async_session_maker
from core.models import Department, Event, FAQNode, KnowledgeBase, Ticket, WebUser
from web.dependencies import require_superadmin
from web.routes.auth import require_crud_rate_limit
from web.security.csrf import get_csrf_token
from web.security.middleware import sanitize_html
from web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_DEPARTMENT_NAME_LEN = 80


async def _department_usage(session, dept_id: int) -> dict:
    """Сколько сущностей связано с отделом (для безопасного удаления)."""
    def _count(model):
        return select(func.count(model.id)).where(model.department_id == dept_id)

    return {
        "tickets": await session.scalar(_count(Ticket)) or 0,
        "knowledge": await session.scalar(_count(KnowledgeBase)) or 0,
        "faq": await session.scalar(_count(FAQNode)) or 0,
        "events": await session.scalar(_count(Event)) or 0,
        "web_users": await session.scalar(_count(WebUser)) or 0,
    }


@router.get("/")
async def departments_page(request: Request, user=Depends(require_superadmin)):
    """Страница управления отделами (только суперадмин)."""
    departments = []
    usage: dict[int, dict] = {}
    db_error = False

    try:
        async with async_session_maker() as session:
            departments = list((await session.execute(
                select(Department).order_by(Department.name)
            )).scalars().all())
            for dept in departments:
                usage[dept.id] = await _department_usage(session, dept.id)
    except Exception as e:
        db_error = True
        logger.error("Не удалось загрузить отделы: %s", e)

    return templates.TemplateResponse(
        "departments.html",
        {
            "request": request,
            "user": user,
            "departments": departments,
            "usage": usage,
            "db_error": db_error,
            "active": "departments",
            "flash_success": request.session.pop("flash_success", None),
            "flash_error": request.session.pop("flash_error", None),
            "csrf_token": get_csrf_token(request),
            "session_id": request.state.session_id,
        },
    )


@router.post("/create")
async def create_department(request: Request, user=Depends(require_superadmin)):
    """Создание отдела (POST, CSRF, только суперадмин)."""
    require_crud_rate_limit(request)
    form = await request.form()
    name = sanitize_html(str(form.get("name", ""))).strip()

    if not name:
        request.session["flash_error"] = "Название отдела не может быть пустым"
        return RedirectResponse(url="/departments/", status_code=303)
    if len(name) > MAX_DEPARTMENT_NAME_LEN:
        request.session["flash_error"] = f"Название отдела длиннее {MAX_DEPARTMENT_NAME_LEN} символов"
        return RedirectResponse(url="/departments/", status_code=303)

    try:
        async with async_session_maker() as session:
            exists = await session.scalar(
                select(func.count(Department.id)).where(Department.name == name)
            )
            if exists:
                request.session["flash_error"] = "Отдел с таким названием уже существует"
                return RedirectResponse(url="/departments/", status_code=303)

            session.add(Department(name=name))
            await session.commit()
    except Exception:
        logger.exception("Не удалось создать отдел")
        request.session["flash_error"] = "Не удалось создать отдел. Попробуйте позже."
        return RedirectResponse(url="/departments/", status_code=303)

    request.session["flash_success"] = f"Отдел «{name}» создан"
    return RedirectResponse(url="/departments/", status_code=303)


@router.post("/{dept_id}/rename")
async def rename_department(request: Request, dept_id: int, user=Depends(require_superadmin)):
    """Переименование отдела (POST, CSRF, только суперадмин)."""
    require_crud_rate_limit(request)
    form = await request.form()
    name = sanitize_html(str(form.get("name", ""))).strip()

    if not name:
        request.session["flash_error"] = "Название отдела не может быть пустым"
        return RedirectResponse(url="/departments/", status_code=303)
    if len(name) > MAX_DEPARTMENT_NAME_LEN:
        request.session["flash_error"] = f"Название отдела длиннее {MAX_DEPARTMENT_NAME_LEN} символов"
        return RedirectResponse(url="/departments/", status_code=303)

    try:
        async with async_session_maker() as session:
            dept = await session.get(Department, dept_id)
            if not dept:
                request.session["flash_error"] = "Отдел не найден"
                return RedirectResponse(url="/departments/", status_code=303)

            exists = await session.scalar(
                select(func.count(Department.id)).where(
                    Department.name == name, Department.id != dept_id
                )
            )
            if exists:
                request.session["flash_error"] = "Отдел с таким названием уже существует"
                return RedirectResponse(url="/departments/", status_code=303)

            dept.name = name
            await session.commit()
    except Exception:
        logger.exception("Не удалось переименовать отдел")
        request.session["flash_error"] = "Не удалось переименовать отдел. Попробуйте позже."
        return RedirectResponse(url="/departments/", status_code=303)

    request.session["flash_success"] = f"Отдел переименован в «{name}»"
    return RedirectResponse(url="/departments/", status_code=303)


@router.post("/{dept_id}/delete")
async def delete_department(request: Request, dept_id: int, user=Depends(require_superadmin)):
    """Удаление пустого отдела (POST, CSRF, только суперадмин).

    Отдел с привязанными данными удалить нельзя — сначала перенесите
    или удалите его контент. Это честная защита, а не тихая потеря данных.
    """
    require_crud_rate_limit(request)
    try:
        async with async_session_maker() as session:
            dept = await session.get(Department, dept_id)
            if not dept:
                request.session["flash_error"] = "Отдел не найден"
                return RedirectResponse(url="/departments/", status_code=303)

            usage = await _department_usage(session, dept_id)
            if any(usage.values()):
                parts = ", ".join(f"{key}: {count}" for key, count in usage.items() if count)
                request.session["flash_error"] = (
                    f"Отдел «{dept.name}» не пуст ({parts}). "
                    "Сначала перенесите или удалите его данные."
                )
                return RedirectResponse(url="/departments/", status_code=303)

            await session.delete(dept)
            await session.commit()
    except Exception:
        logger.exception("Не удалось удалить отдел")
        request.session["flash_error"] = "Не удалось удалить отдел. Попробуйте позже."
        return RedirectResponse(url="/departments/", status_code=303)

    request.session["flash_success"] = "Отдел удалён"
    return RedirectResponse(url="/departments/", status_code=303)
