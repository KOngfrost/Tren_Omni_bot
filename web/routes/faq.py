"""
Маршруты FAQ.

Безопасность:
- CSRF защищён middleware CSRFMiddleware в main.py
- IDOR: админ видит только FAQ своего отдела (суперадмин — все)
- Санитизация входных данных от XSS
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from core.bulk_import import (
    export_faq_xlsx_async,
    generate_faq_template_csv_async,
    generate_faq_template_xlsx_async,
    parse_faq_rows_async,
    parse_file_or_text_async,
)
from core.database import async_session_maker
from core.models import Department, FAQNode, Log
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
        logger.error("Не удалось загрузить частые вопросы: %s", e)

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
    """Добавление нового вопроса/ответа FAQ.

    Упрощённая модель: отдел (автор), конкретный вопрос и ответ.
    Если ответ заполнен, элемент автоматически считается конечным (is_final = True).
    """
    form = await request.form()
    # Санитизация входных данных для защиты от XSS
    question: str = sanitize_html(str(form.get("question", ""))).strip()
    final_answer_raw = str(form.get("final_answer", "")).strip()
    final_answer: str | None = sanitize_html(final_answer_raw) if final_answer_raw else None
    is_final: bool = form.get("is_final") == "on" or bool(final_answer)

    if not question:
        request.session["flash_error"] = "Текст вопроса обязателен"
        return RedirectResponse(url="/faq/", status_code=303)

    if is_final and not final_answer:
        request.session["flash_error"] = "Для конечного элемента нужен ответ"
        return RedirectResponse(url="/faq/", status_code=303)

    try:
        async with async_session_maker() as session:
            is_super, dept_id = await get_admin_scope(session, user)
            department_id: int | None = parse_form_int(form, "department_id", default=dept_id)
            if not is_super:
                department_id = dept_id
            if department_id is None:
                request.session["flash_error"] = "Не выбран отдел"
                return RedirectResponse(url="/faq/", status_code=303)

            parent_id: int | None = parse_form_int(form, "parent_id")
            if parent_id is not None:
                parent = await session.get(FAQNode, parent_id)
                if parent is None or parent.department_id != department_id:
                    request.session["flash_error"] = "Родительский вопрос не найден"
                    return RedirectResponse(url="/faq/", status_code=303)

            max_order = await session.scalar(
                select(func.coalesce(func.max(FAQNode.order_index), 0)).where(
                    FAQNode.department_id == department_id
                )
            )
            session.add(
                FAQNode(
                    department_id=department_id,
                    parent_id=parent_id,
                    question=question,
                    is_final=is_final,
                    final_answer=final_answer,
                    order_index=(max_order or 0) + 1,
                )
            )
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

    request.session["flash_success"] = "Элемент удалён"
    return RedirectResponse(url="/faq/", status_code=303)


@router.get("/template.xlsx")
async def faq_template_xlsx(user=Depends(require_auth)):
    """Скачать шаблон Excel (.xlsx) для массовой загрузки частых вопросов."""
    # openpyxl — синхронная ресурсоёмкая библиотека: async API bulk_import
    # выполняет её в пуле потоков, event loop не блокируется (Ошибка #11).
    data = await generate_faq_template_xlsx_async()
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=template_chastye_voprosy.xlsx"},
    )


@router.get("/template.csv")
async def faq_template_csv(user=Depends(require_auth)):
    """Скачать шаблон CSV для массовой загрузки частых вопросов."""
    data = (await generate_faq_template_csv_async()).encode("utf-8-sig")
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=template_chastye_voprosy.csv"},
    )


@router.get("/export.xlsx")
async def faq_export_xlsx(request: Request, user=Depends(require_auth)):
    """Экспорт текущих частых вопросов в Excel."""
    async with async_session_maker() as session:
        is_super, dept_id = await get_admin_scope(session, user)
        stmt = (
            select(FAQNode)
            .options(selectinload(FAQNode.department))
            .order_by(FAQNode.department_id, FAQNode.order_index, FAQNode.id)
        )
        if not is_super:
            stmt = stmt.where(FAQNode.department_id == dept_id)
        nodes = list((await session.execute(stmt)).scalars().all())

    # openpyxl — синхронная ресурсоёмкая библиотека: async API bulk_import
    # выполняет её в пуле потоков, event loop не блокируется (Ошибка #11).
    data = await export_faq_xlsx_async(nodes)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=chastye_voprosy_export.xlsx"},
    )


@router.post("/import")
async def import_faq(request: Request, user=Depends(require_writer)):
    """Массовая загрузка частых вопросов из файла Excel/CSV или текстовой вставки."""
    form = await request.form()
    file_upload = form.get("file")
    file_bytes: bytes | None = None
    filename: str | None = None

    if file_upload and hasattr(file_upload, "read") and getattr(file_upload, "filename", None):
        file_bytes = await file_upload.read()
        filename = file_upload.filename

    text_data = str(form.get("text_data", "")).strip()

    if not file_bytes and not text_data:
        request.session["flash_error"] = "Выберите файл или вставьте строки с вопросами и ответами"
        return RedirectResponse(url="/faq/", status_code=303)

    try:
        # openpyxl.load_workbook / csv.reader — синхронные ресурсоёмкие
        # операции: async API bulk_import выносит их в пул потоков, поэтому
        # разбор большого файла не блокирует event loop (Ошибка #11).
        raw_rows = await parse_file_or_text_async(
            file_bytes, filename, text_data if text_data else None
        )
        parsed = await parse_faq_rows_async(raw_rows)
    except Exception as e:
        logger.warning("Ошибка разбора файла частых вопросов: %s", e)
        request.session["flash_error"] = f"Не удалось прочитать файл: {e}"
        return RedirectResponse(url="/faq/", status_code=303)

    if not parsed:
        request.session["flash_error"] = (
            "Не найдено корректных строк с вопросами и ответами для загрузки"
        )
        return RedirectResponse(url="/faq/", status_code=303)

    try:
        async with async_session_maker() as session:
            is_super, dept_id = await get_admin_scope(session, user)
            form_dept_id = parse_form_int(form, "department_id", default=dept_id)

            depts = list((await session.execute(select(Department))).scalars().all())
            dept_map = {d.name.lower().strip(): d.id for d in depts}

            count = 0
            for item in parsed:
                q = sanitize_html(item["question"]).strip()
                a = sanitize_html(item["answer"]).strip()
                row_dept = item.get("department", "").lower().strip()

                target_dept_id = dept_id if not is_super else None
                if target_dept_id is None and row_dept:
                    for d_name, d_id in dept_map.items():
                        if row_dept in d_name or d_name in row_dept:
                            target_dept_id = d_id
                            break

                if target_dept_id is None:
                    target_dept_id = form_dept_id or (depts[0].id if depts else None)

                if q and a and target_dept_id:
                    session.add(
                        FAQNode(
                            department_id=target_dept_id,
                            question=q,
                            final_answer=a,
                            is_final=True,
                        )
                    )
                    count += 1

            session.add(
                Log(
                    user_id=getattr(user, "admin_id", None),
                    action="faq_bulk_import",
                    details=f"Массовая загрузка частых вопросов: успешно добавлено {count} записей",
                )
            )
            await session.commit()
    except Exception:
        logger.exception("Ошибка сохранения частых вопросов при импорте")
        request.session["flash_error"] = "Ошибка при сохранении данных в базу. Попробуйте позже."
        return RedirectResponse(url="/faq/", status_code=303)

    request.session["flash_success"] = f"Успешно загружено {count} вопросов"
    return RedirectResponse(url="/faq/", status_code=303)
