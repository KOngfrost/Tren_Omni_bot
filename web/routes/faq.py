"""
Маршруты FAQ.

Безопасность:
- CSRF защищён middleware CSRFMiddleware в main.py
- IDOR: админ видит только FAQ своего отдела (суперадмин — все)
- Санитизация входных данных от XSS
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from core.database import async_session_maker
from core.models import Department, FAQNode
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


def _attach_depths(nodes: list["FAQNode"]) -> None:
    """Проставить node.depth (глубина в дереве FAQ) для отрисовки вложенности.

    Узлы без существующего родителя считаются корневыми (в БД parent_id
    обнуляется при удалении родителя — ondelete SET NULL).
    """
    by_id = {node.id: node for node in nodes}
    for node in nodes:
        node.depth = 0 if node.parent_id not in by_id else -1  # -1 = требуется расчёт

    for _ in range(len(nodes) + 1):
        unresolved = False
        for node in nodes:
            if node.depth == -1:
                parent = by_id.get(node.parent_id)
                if parent is not None and parent.depth >= 0:
                    node.depth = parent.depth + 1
                else:
                    unresolved = True
        if not unresolved:
            break

    for node in nodes:
        if node.depth < 0:  # защита от циклов в данных
            node.depth = 0

    nodes.sort(key=lambda n: (n.depth, n.order_index, n.id))


@router.get("/")
async def faq_page(request: Request, user=Depends(require_auth)):
    """Страница FAQ-дерева с IDOR-защитой."""
    faq_nodes: list[FAQNode] = []
    departments: list[Department] = []
    db_error: bool = False

    try:
        async with async_session_maker() as session:
            is_super, dept_id = await get_admin_scope(session, user)
            departments = await get_departments_for_user(session, user)

            faq_stmt = (
                select(FAQNode)
                .options(selectinload(FAQNode.department))
                .order_by(FAQNode.department_id, FAQNode.order_index, FAQNode.id)
            )
            if not is_super:
                faq_stmt = faq_stmt.where(FAQNode.department_id == dept_id)
            faq_nodes = list((await session.execute(faq_stmt)).scalars().all())
            _attach_depths(faq_nodes)
    except Exception as e:
        db_error = True
        logger.error("Не удалось загрузить FAQ: %s", e)

    return templates.TemplateResponse(
        "faq.html",
        {
            "request": request,
            "user": user,
            "faq_nodes": faq_nodes,
            "departments": departments,
            "db_error": db_error,
            "active": "faq",
            "flash_success": request.session.pop("flash_success", None),
            "flash_error": request.session.pop("flash_error", None),
            "csrf_token": get_csrf_token(request),
            "session_id": request.state.session_id,
        },
    )



@router.post("/")
async def add_faq(request: Request, user=Depends(require_writer)):
    """Добавление элемента FAQ с санитизацией входных данных."""
    form = await request.form()
    question: str = sanitize_html(str(form.get("question", "")))
    is_final: bool = form.get("is_final") == "on"
    final_answer: str | None = sanitize_html(str(form.get("final_answer", ""))) if is_final else None

    if not question.strip():
        request.session["flash_error"] = "Текст вопроса обязателен"
        return RedirectResponse(url="/faq/", status_code=303)
    if is_final and not (final_answer or "").strip():
        request.session["flash_error"] = "Для конечного элемента нужен ответ"
        return RedirectResponse(url="/faq/", status_code=303)

    try:
        async with async_session_maker() as session:
            is_super, dept_id = await get_admin_scope(session, user)
            department_id: int | None = parse_form_int(form, "department_id", default=dept_id)
            if not is_super:
                # Админ отдела жёстко привязан к своему отделу
                department_id = dept_id
            if department_id is None:
                request.session["flash_error"] = "Не выбран отдел"
                return RedirectResponse(url="/faq/", status_code=303)

            parent_id: int | None = parse_form_int(form, "parent_id")
            if parent_id is not None:
                # Родитель должен существовать и принадлежать тому же отделу
                parent = await session.get(FAQNode, parent_id)
                if parent is None or parent.department_id != department_id:
                    request.session["flash_error"] = "Родительский вопрос не найден"
                    return RedirectResponse(url="/faq/", status_code=303)

            max_order = await session.scalar(
                select(func.coalesce(func.max(FAQNode.order_index), 0))
                .where(FAQNode.department_id == department_id)
            )
            session.add(FAQNode(
                department_id=department_id,
                parent_id=parent_id,
                question=question,
                is_final=is_final,
                final_answer=final_answer,
                order_index=(max_order or 0) + 1,
            ))
            await session.commit()
    except Exception:
        logger.exception("Не удалось добавить FAQ")
        request.session["flash_error"] = "Не удалось сохранить FAQ. Попробуйте позже."
        return RedirectResponse(url="/faq/", status_code=303)

    request.session["flash_success"] = "Элемент FAQ добавлен"
    return RedirectResponse(url="/faq/", status_code=303)


@router.post("/{node_id}/delete")
async def delete_faq(request: Request, node_id: int, user=Depends(require_writer)):
    """Удаление элемента FAQ (POST с CSRF-токеном) с проверкой прав."""
    try:
        async with async_session_maker() as session:
            node = await session.get(FAQNode, node_id)
            if not node:
                request.session["flash_error"] = "Элемент FAQ не найден"
                return RedirectResponse(url="/faq/", status_code=303)

            # IDOR: админ отдела может удалять только FAQ своего отдела
            is_super, dept_id = await get_admin_scope(session, user)
            if not is_super and node.department_id != dept_id:
                request.session["flash_error"] = "Нет прав для удаления этого элемента FAQ"
                return RedirectResponse(url="/faq/", status_code=303)

            await session.delete(node)
            await session.commit()
    except Exception:
        logger.exception("Не удалось удалить FAQ")
        request.session["flash_error"] = "Не удалось удалить FAQ. Попробуйте позже."
        return RedirectResponse(url="/faq/", status_code=303)

    request.session["flash_success"] = "Элемент FAQ удалён"
    return RedirectResponse(url="/faq/", status_code=303)
