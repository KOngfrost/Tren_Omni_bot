"""
Маршруты базы знаний.

Безопасность:
- CSRF защищён middleware CSRFMiddleware в main.py
- IDOR: админ видит только записи своего отдела (суперадмин — все)
- Санитизация входных данных от XSS
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.database import async_session_maker
from core.models import Department, KnowledgeBase
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
async def knowledge_base_page(request: Request, user=Depends(require_auth)):
    """Страница базы знаний с IDOR-защитой."""
    knowledge_base: list[KnowledgeBase] = []
    departments: list[Department] = []
    db_error: bool = False

    try:
        async with async_session_maker() as session:
            is_super, dept_id = await get_admin_scope(session, user)
            departments = await get_departments_for_user(session, user)

            kb_stmt = (
                select(KnowledgeBase)
                .options(selectinload(KnowledgeBase.department))
                .order_by(KnowledgeBase.id)
            )
            if not is_super:
                kb_stmt = kb_stmt.where(
                    (KnowledgeBase.department_id == dept_id)
                    | (KnowledgeBase.department_id.is_(None))
                )
            knowledge_base = list((await session.execute(kb_stmt)).scalars().all())
    except Exception as e:
        db_error = True
        logger.error("Не удалось загрузить базу знаний: %s", e)

    return templates.TemplateResponse(
        "knowledge_base.html",
        {
            "request": request,
            "user": user,
            "knowledge_base": knowledge_base,
            "departments": departments,
            "db_error": db_error,
            "active": "knowledge",
            "flash_success": request.session.pop("flash_success", None),
            "flash_error": request.session.pop("flash_error", None),
            "csrf_token": get_csrf_token(request),
            "session_id": request.state.session_id,
        },
    )


@router.post("/")
async def add_knowledge_base(request: Request, user=Depends(require_writer)):
    """Добавление записи в базу знаний.

    Безопасность:
    - Проверка прав на запись (require_writer)
    - Санитизация входных данных от XSS
    """
    form = await request.form()
    keywords: str = sanitize_html(str(form.get("keywords", "")))
    answer: str = sanitize_html(str(form.get("answer", "")))
    if not keywords.strip() or not answer.strip():
        request.session["flash_error"] = "Ключевые слова и ответ не могут быть пустыми"
        return RedirectResponse(url="/knowledge/", status_code=303)

    try:
        async with async_session_maker() as session:
            is_super, dept_id = await get_admin_scope(session, user)
            department_id: int | None = parse_form_int(form, "department_id", default=dept_id)

            if not is_super:
                # Админ отдела жёстко привязан к своему отделу
                department_id = dept_id
            if department_id is None:
                request.session["flash_error"] = "Не выбран отдел"
                return RedirectResponse(url="/knowledge/", status_code=303)

            session.add(KnowledgeBase(
                department_id=department_id,
                keywords=keywords,
                answer=answer,
            ))
            await session.commit()
    except Exception:
        logger.exception("Не удалось добавить запись в базу знаний")
        request.session["flash_error"] = "Не удалось сохранить запись. Попробуйте позже."
        return RedirectResponse(url="/knowledge/", status_code=303)

    request.session["flash_success"] = "Запись добавлена в базу знаний"
    return RedirectResponse(url="/knowledge/", status_code=303)


@router.post("/{kb_id}/delete")
async def delete_knowledge_base(request: Request, kb_id: int, user=Depends(require_writer)):
    """Удаление записи из базы знаний (POST с CSRF-токеном) с проверкой прав."""
    try:
        async with async_session_maker() as session:
            kb = await session.get(KnowledgeBase, kb_id)
            if not kb:
                request.session["flash_error"] = "Запись не найдена"
                return RedirectResponse(url="/knowledge/", status_code=303)

            # IDOR: админ отдела может удалять только записи своего отдела
            is_super, dept_id = await get_admin_scope(session, user)
            if not is_super and kb.department_id != dept_id:
                request.session["flash_error"] = "Нет прав для удаления этой записи"
                return RedirectResponse(url="/knowledge/", status_code=303)

            await session.delete(kb)
            await session.commit()
    except Exception:
        logger.exception("Не удалось удалить запись базы знаний")
        request.session["flash_error"] = "Не удалось удалить запись. Попробуйте позже."
        return RedirectResponse(url="/knowledge/", status_code=303)

    request.session["flash_success"] = "Запись удалена"
    return RedirectResponse(url="/knowledge/", status_code=303)

