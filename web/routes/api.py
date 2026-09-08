"""
JSON API эндпоинты для динамического обновления данных без перезагрузки страницы.

Обеспечивает:
- Единый источник правды для отделов (получение, создание, переименование, удаление)
- Корректную сериализацию данных с обработкой NULL/пустых полей
- Стандартизированные ответы: {success: bool, data?: any, error?: string}
- Обработку ошибок БД с информативными сообщениями
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from core.database import async_session_maker
from core.models import Department, Event, FAQNode, KnowledgeBase, Ticket, WebUser
from web.dependencies import require_superadmin
from web.routes.auth import require_crud_rate_limit
from web.security.middleware import sanitize_html

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_DEPARTMENT_NAME_LEN = 80


def api_success(data=None):
    """Стандартный успешный ответ API."""
    return JSONResponse({"success": True, "data": data})


def api_error(message, status_code=400):
    """Стандартный ошибочный ответ API."""
    return JSONResponse({"success": False, "error": message}, status_code=status_code)


def _serialize_department(dept, usage=None):
    """Сериализовать отдел в словарь для JSON с обработкой NULL."""
    result = {
        "id": dept.id,
        "name": dept.name or "",
        "created_at": dept.created_at.isoformat() if dept.created_at else None,
    }
    if usage is not None:
        result["usage"] = usage
    return result


async def _get_department_usage(session, dept_id):
    """Получить статистику использования отдела."""
    return {
        "tickets": (
            await session.scalar(
                select(func.count(Ticket.id)).where(Ticket.department_id == dept_id)
            ) or 0
        ),
        "knowledge": (
            await session.scalar(
                select(func.count(KnowledgeBase.id)).where(
                    KnowledgeBase.department_id == dept_id
                )
            ) or 0
        ),
        "faq": (
            await session.scalar(
                select(func.count(FAQNode.id)).where(FAQNode.department_id == dept_id)
            ) or 0
        ),
        "events": (
            await session.scalar(
                select(func.count(Event.id)).where(Event.department_id == dept_id)
            ) or 0
        ),
        "web_users": (
            await session.scalar(
                select(func.count(WebUser.id)).where(WebUser.department_id == dept_id)
            ) or 0
        ),
    }


# ==========================================
# API: Получение списка отделов
# ==========================================


@router.get("/departments/")
async def api_get_departments(user=Depends(require_superadmin)):
    """Получить список всех отделов с статистикой.

    Используется клиентским store для синхронизации состояния.
    """
    try:
        async with async_session_maker() as session:
            departments = list(
                (await session.execute(select(Department).order_by(Department.name)))
                .scalars()
                .all()
            )
            result = []
            for dept in departments:
                usage = await _get_department_usage(session, dept.id)
                result.append(_serialize_department(dept, usage))
            return api_success(result)
    except Exception as e:
        logger.error("API: не удалось загрузить отделы: %s", e)
        return api_error("Не удалось загрузить отделы. Попробуйте позже.", 500)


@router.get("/departments/{dept_id}/")
async def api_get_department(dept_id: int, user=Depends(require_superadmin)):
    """Получить данные одного отдела по ID."""
    try:
        async with async_session_maker() as session:
            dept = await session.get(Department, dept_id)
            if not dept:
                return api_error("Отдел не найден", 404)
            usage = await _get_department_usage(session, dept.id)
            return api_success(_serialize_department(dept, usage))
    except Exception as e:
        logger.error("API: не удалось загрузить отдел %s: %s", dept_id, e)
        return api_error("Не удалось загрузить данные отдела.", 500)


# ==========================================
# API: Создание отдела
# ==========================================


@router.post("/departments/create")
async def api_create_department(request: Request, user=Depends(require_superadmin)):
    """Создать новый отдел.

    Принимает JSON: {"name": "Название отдела"}
    Возвращает: {"success": true, "data": {id, name, usage}}
    """
    require_crud_rate_limit(request)

    try:
        body = await request.json()
    except Exception:
        return api_error("Некорректный формат запроса (ожидается JSON)")

    name = sanitize_html(str(body.get("name", ""))).strip()

    if not name:
        return api_error("Название отдела не может быть пустым")
    if len(name) > MAX_DEPARTMENT_NAME_LEN:
        return api_error(f"Название длиннее {MAX_DEPARTMENT_NAME_LEN} символов")

    try:
        async with async_session_maker() as session:
            exists = await session.scalar(
                select(func.count(Department.id)).where(Department.name == name)
            )
            if exists:
                return api_error("Отдел с таким названием уже существует")

            dept = Department(name=name)
            session.add(dept)
            await session.flush()
            await session.commit()

            await session.refresh(dept)
            usage = await _get_department_usage(session, dept.id)
            logger.info("API: создан отдел id=%s, name=%s", dept.id, dept.name)
            return api_success(_serialize_department(dept, usage))
    except Exception as e:
        logger.exception("API: не удалось создать отдел")
        return api_error(f"Ошибка создания отдела: {str(e)}", 500)


# ==========================================
# API: Переименование отдела
# ==========================================


@router.post("/departments/{dept_id}/rename")
async def api_rename_department(
    dept_id: int, request: Request, user=Depends(require_superadmin)
):
    """Переименовать отдел. Принимает JSON: {"name": "Новое название"}"""
    require_crud_rate_limit(request)

    try:
        body = await request.json()
    except Exception:
        return api_error("Некорректный формат запроса")

    name = sanitize_html(str(body.get("name", ""))).strip()

    if not name:
        return api_error("Название отдела не может быть пустым")
    if len(name) > MAX_DEPARTMENT_NAME_LEN:
        return api_error(f"Название длиннее {MAX_DEPARTMENT_NAME_LEN} символов")

    try:
        async with async_session_maker() as session:
            dept = await session.get(Department, dept_id)
            if not dept:
                return api_error("Отдел не найден", 404)

            exists = await session.scalar(
                select(func.count(Department.id)).where(
                    Department.name == name, Department.id != dept_id
                )
            )
            if exists:
                return api_error("Отдел с таким названием уже существует")

            dept.name = name
            await session.commit()

            usage = await _get_department_usage(session, dept.id)
            logger.info("API: отдел id=%s переименован в '%s'", dept_id, name)
            return api_success(_serialize_department(dept, usage))
    except Exception as e:
        logger.exception("API: не удалось переименовать отдел %s", dept_id)
        return api_error(f"Ошибка переименования: {str(e)}", 500)


# ==========================================
# API: Удаление отдела
# ==========================================


@router.post("/departments/{dept_id}/delete")
async def api_delete_department(
    dept_id: int, request: Request, user=Depends(require_superadmin)
):
    """Удалить пустой отдел. Отдел с данными удалить нельзя."""
    require_crud_rate_limit(request)

    try:
        async with async_session_maker() as session:
            dept = await session.get(Department, dept_id)
            if not dept:
                return api_error("Отдел не найден", 404)

            usage = await _get_department_usage(session, dept_id)
            if any(usage.values()):
                parts = ", ".join(f"{k}: {v}" for k, v in usage.items() if v)
                return api_error(
                    f"Отдел «{dept.name}» не пуст ({parts}). "
                    "Сначала перенесите или удалите его данные."
                )

            dept_name = dept.name
            await session.delete(dept)
            await session.commit()

            logger.info("API: удалён отдел id=%s, name=%s", dept_id, dept_name)
            return api_success({"id": dept_id, "deleted": True})
    except Exception as e:
        logger.exception("API: не удалось удалить отдел %s", dept_id)
        return api_error(f"Ошибка удаления: {str(e)}", 500)
