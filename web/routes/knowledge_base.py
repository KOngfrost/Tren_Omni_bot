"""
Маршруты базы знаний.

Безопасность:
- CSRF защищён middleware CSRFMiddleware в main.py
- IDOR: админ видит только записи своего отдела (суперадмин — все)
- Санитизация входных данных от XSS
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.bulk_import import (
    export_knowledge_xlsx,
    generate_knowledge_template_csv,
    generate_knowledge_template_xlsx,
    parse_file_or_text,
    parse_knowledge_rows,
)
from core.database import async_session_maker
from core.models import Department, KnowledgeBase, Log
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

            session.add(
                KnowledgeBase(
                    department_id=department_id,
                    keywords=keywords,
                    answer=answer,
                )
            )
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


@router.get("/template.xlsx")
async def knowledge_template_xlsx(user=Depends(require_auth)):
    """Скачать шаблон Excel (.xlsx) для массовой загрузки базы знаний."""
    # openpyxl — синхронная ресурсоёмкая библиотека: выносим в фоновый поток,
    # чтобы не блокировать event loop (Ошибка #11).
    data = await asyncio.to_thread(generate_knowledge_template_xlsx)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=template_baza_znaniy.xlsx"},
    )


@router.get("/template.csv")
async def knowledge_template_csv(user=Depends(require_auth)):
    """Скачать шаблон CSV для массовой загрузки базы знаний."""
    data = generate_knowledge_template_csv().encode("utf-8-sig")
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=template_baza_znaniy.csv"},
    )


@router.get("/export.xlsx")
async def knowledge_export_xlsx(request: Request, user=Depends(require_auth)):
    """Экспорт текущих записей базы знаний в Excel."""
    async with async_session_maker() as session:
        is_super, dept_id = await get_admin_scope(session, user)
        stmt = (
            select(KnowledgeBase)
            .options(selectinload(KnowledgeBase.department))
            .order_by(KnowledgeBase.id)
        )
        if not is_super:
            stmt = stmt.where(
                (KnowledgeBase.department_id == dept_id) | (KnowledgeBase.department_id.is_(None))
            )
        items = list((await session.execute(stmt)).scalars().all())

    # openpyxl — синхронная ресурсоёмкая библиотека: выносим в фоновый поток,
    # чтобы не блокировать event loop (Ошибка #11).
    data = await asyncio.to_thread(export_knowledge_xlsx, items)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=baza_znaniy_export.xlsx"},
    )


@router.post("/import")
async def import_knowledge_base(request: Request, user=Depends(require_writer)):
    """Массовая загрузка базы знаний из файла Excel/CSV или текстовой вставки."""
    form = await request.form()
    file_upload = form.get("file")
    file_bytes: bytes | None = None
    filename: str | None = None

    if file_upload and hasattr(file_upload, "read") and getattr(file_upload, "filename", None):
        file_bytes = await file_upload.read()
        filename = file_upload.filename

    text_data = str(form.get("text_data", "")).strip()

    if not file_bytes and not text_data:
        request.session["flash_error"] = "Выберите файл или вставьте строки с материалами"
        return RedirectResponse(url="/knowledge/", status_code=303)

    try:
        # openpyxl.load_workbook — синхронная ресурсоёмкая операция: выносим
        # в фоновый поток, чтобы не блокировать event loop (Ошибка #11).
        raw_rows = await asyncio.to_thread(
            parse_file_or_text, file_bytes, filename, text_data if text_data else None
        )
        parsed = parse_knowledge_rows(raw_rows)
    except Exception as e:
        logger.warning("Ошибка разбора файла базы знаний: %s", e)
        request.session["flash_error"] = f"Не удалось прочитать файл: {e}"
        return RedirectResponse(url="/knowledge/", status_code=303)

    if not parsed:
        request.session["flash_error"] = "Не найдено корректных строк с материалами для загрузки"
        return RedirectResponse(url="/knowledge/", status_code=303)

    try:
        async with async_session_maker() as session:
            is_super, dept_id = await get_admin_scope(session, user)
            form_dept_id = parse_form_int(form, "department_id", default=dept_id)

            depts = list((await session.execute(select(Department))).scalars().all())
            dept_map = {d.name.lower().strip(): d.id for d in depts}

            count = 0
            for item in parsed:
                k = sanitize_html(item["keywords"]).strip()
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

                if k and a and target_dept_id:
                    session.add(
                        KnowledgeBase(
                            department_id=target_dept_id,
                            keywords=k,
                            answer=a,
                        )
                    )
                    count += 1

            session.add(
                Log(
                    user_id=getattr(user, "admin_id", None),
                    action="knowledge_bulk_import",
                    details=f"Массовая загрузка базы знаний: успешно добавлено {count} записей",
                )
            )
            await session.commit()
    except Exception:
        logger.exception("Ошибка сохранения базы знаний при импорте")
        request.session["flash_error"] = "Ошибка при сохранении данных в базу. Попробуйте позже."
        return RedirectResponse(url="/knowledge/", status_code=303)

    request.session["flash_success"] = f"Успешно загружено {count} материалов базы знаний"
    return RedirectResponse(url="/knowledge/", status_code=303)
