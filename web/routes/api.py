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
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload

from core.database import async_session_maker
from core.models import (
    Department,
    Event,
    FAQNode,
    KnowledgeBase,
    PartnershipRequest,
    Subscription,
    Ticket,
    TicketStatus,
    WebUser,
)
from web.dependencies import get_admin_scope, require_auth, require_superadmin
from web.routes.auth import require_crud_rate_limit
from web.schemas import MAX_DEPARTMENT_NAME_LEN, DepartmentNamePayload
from web.security.middleware import sanitize_html

logger = logging.getLogger(__name__)

router = APIRouter()


def api_success(data=None):
    """Стандартный успешный ответ API."""
    return JSONResponse({"success": True, "data": data})


def api_error(message, status_code=400):
    """Стандартный ошибочный ответ API."""
    return JSONResponse({"success": False, "error": message}, status_code=status_code)


def _serialize_department(dept, usage=None):
    """Сериализовать отдел в словарь для JSON с обработкой NULL.

    created_at читается через getattr: у модели Department исторически нет
    этой колонки (есть только id и name), а раньше прямой доступ
    dept.created_at падал с AttributeError → 500 на create/rename.
    Если колонку добавят миграцией, сериализатор подхватит её автоматически.
    """
    created_at = getattr(dept, "created_at", None)
    result = {
        "id": dept.id,
        "name": dept.name or "",
        "created_at": created_at.isoformat() if created_at else None,
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
            )
            or 0
        ),
        "knowledge": (
            await session.scalar(
                select(func.count(KnowledgeBase.id)).where(KnowledgeBase.department_id == dept_id)
            )
            or 0
        ),
        "faq": (
            await session.scalar(
                select(func.count(FAQNode.id)).where(FAQNode.department_id == dept_id)
            )
            or 0
        ),
        "events": (
            await session.scalar(
                select(func.count(Event.id)).where(Event.department_id == dept_id)
            )
            or 0
        ),
        "web_users": (
            await session.scalar(
                select(func.count(WebUser.id)).where(WebUser.department_id == dept_id)
            )
            or 0
        ),
        "subscriptions": (
            await session.scalar(
                select(func.count(Subscription.id)).where(Subscription.department_id == dept_id)
            )
            or 0
        ),
    }


async def _get_all_departments_usage(session, dept_ids):
    """Получить статистику для всех отделов одним запросом.

    Возвращает dict: dept_id -> {tickets, knowledge, faq, events, web_users}.
    Для отделов без данных возвращается dict с нулями.
    """
    if not dept_ids:
        return {}

    usage_map: dict[int, dict[str, int]] = {
        dept_id: {
            "tickets": 0,
            "knowledge": 0,
            "faq": 0,
            "events": 0,
            "web_users": 0,
        }
        for dept_id in dept_ids
    }

    # Tickets
    results = await session.execute(
        select(Ticket.department_id, func.count(Ticket.id))
        .where(Ticket.department_id.in_(dept_ids))
        .group_by(Ticket.department_id)
    )
    for dept_id, count in results:
        if dept_id in usage_map:
            usage_map[dept_id]["tickets"] = count

    # KnowledgeBase
    results = await session.execute(
        select(KnowledgeBase.department_id, func.count(KnowledgeBase.id))
        .where(KnowledgeBase.department_id.in_(dept_ids))
        .group_by(KnowledgeBase.department_id)
    )
    for dept_id, count in results:
        if dept_id in usage_map:
            usage_map[dept_id]["knowledge"] = count

    # FAQNode
    results = await session.execute(
        select(FAQNode.department_id, func.count(FAQNode.id))
        .where(FAQNode.department_id.in_(dept_ids))
        .group_by(FAQNode.department_id)
    )
    for dept_id, count in results:
        if dept_id in usage_map:
            usage_map[dept_id]["faq"] = count

    # Event
    results = await session.execute(
        select(Event.department_id, func.count(Event.id))
        .where(Event.department_id.in_(dept_ids))
        .group_by(Event.department_id)
    )
    for dept_id, count in results:
        if dept_id in usage_map:
            usage_map[dept_id]["events"] = count

    # WebUser
    results = await session.execute(
        select(WebUser.department_id, func.count(WebUser.id))
        .where(WebUser.department_id.in_(dept_ids))
        .group_by(WebUser.department_id)
    )
    for dept_id, count in results:
        if dept_id in usage_map:
            usage_map[dept_id]["web_users"] = count

    return usage_map


# ==========================================
# API: Получение списка отделов
# ==========================================


@router.get("/departments/")
async def api_get_departments(user=Depends(require_auth)):
    """Получить список всех отделов с статистикой.

    Используется клиентским store для синхронизации состояния.
    """
    try:
        async with async_session_maker() as session:
            is_super, dept_id = await get_admin_scope(session, user)
            department_stmt = select(Department).order_by(Department.name)
            if not is_super:
                department_stmt = department_stmt.where(Department.id == dept_id)
            departments = list((await session.execute(department_stmt)).scalars().all())
            dept_ids = [d.id for d in departments]
            usage_map = await _get_all_departments_usage(session, dept_ids)
            result = [_serialize_department(dept, usage_map.get(dept.id)) for dept in departments]
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
            usage_map = await _get_all_departments_usage(session, [dept_id])
            return api_success(_serialize_department(dept, usage_map.get(dept_id)))
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
    await require_crud_rate_limit(request)

    try:
        # Pydantic-схема (web/schemas.py): невалидное тело (массив вместо
        # объекта, null в name) даёт 400, а не AttributeError → 500.
        payload = DepartmentNamePayload.model_validate(await request.json())
    except Exception:
        return api_error("Некорректный формат запроса (ожидается JSON)")

    name = sanitize_html(payload.name).strip()

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
            usage_map = await _get_all_departments_usage(session, [dept.id])
            logger.info("API: создан отдел id=%s, name=%s", dept.id, dept.name)
            return api_success(_serialize_department(dept, usage_map.get(dept.id)))
    except Exception:
        logger.exception("API: не удалось создать отдел")
        return api_error("Не удалось создать отдел", 500)


# ==========================================
# API: Переименование отдела
# ==========================================


@router.post("/departments/{dept_id}/rename")
async def api_rename_department(dept_id: int, request: Request, user=Depends(require_superadmin)):
    """Переименовать отдел. Принимает JSON: {"name": "Новое название"}"""
    await require_crud_rate_limit(request)

    try:
        payload = DepartmentNamePayload.model_validate(await request.json())
    except Exception:
        return api_error("Некорректный формат запроса")

    name = sanitize_html(payload.name).strip()

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

            usage_map = await _get_all_departments_usage(session, [dept.id])
            logger.info("API: отдел id=%s переименован в '%s'", dept_id, name)
            return api_success(_serialize_department(dept, usage_map.get(dept.id)))
    except Exception:
        logger.exception("API: не удалось переименовать отдел %s", dept_id)
        return api_error("Не удалось переименовать отдел", 500)


# ==========================================
# API: Удаление отдела
# ==========================================


@router.post("/departments/{dept_id}/delete")
async def api_delete_department(dept_id: int, request: Request, user=Depends(require_superadmin)):
    """Удалить пустой отдел. Отдел с данными удалить нельзя."""
    await require_crud_rate_limit(request)

    try:
        async with async_session_maker() as session:
            dept = await session.get(Department, dept_id)
            if not dept:
                return api_error("Отдел не найден", 404)

            usage_map = await _get_all_departments_usage(session, [dept_id])
            usage = usage_map.get(dept_id, {})
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
    except Exception:
        logger.exception("API: не удалось удалить отдел %s", dept_id)
        return api_error("Не удалось удалить отдел", 500)


# ==========================================
# API: Счётчики новых заявок и партнёрств (🔥)
# ==========================================


@router.get("/counters")
async def api_get_counters(request: Request, user=Depends(require_auth)):
    """Получить детальные раздельные счётчики и список активных заявок/ответов/партнёрств."""
    new_tickets_count = 0
    student_replies_count = 0
    new_partnerships_count = 0
    items: list[dict] = []

    try:
        async with async_session_maker() as session:
            is_super, dept_id = await get_admin_scope(session, user)

            base_scope = [Ticket.status == TicketStatus.NEW]
            if not is_super and dept_id:
                base_scope.append(Ticket.department_id == dept_id)

            new_scope = base_scope + [or_(Ticket.response_text.is_(None), Ticket.response_text == "")]
            reply_scope = base_scope + [and_(Ticket.response_text.is_not(None), Ticket.response_text != "")]

            new_tickets_count = (
                await session.scalar(select(func.count(Ticket.id)).where(*new_scope))
            ) or 0

            student_replies_count = (
                await session.scalar(select(func.count(Ticket.id)).where(*reply_scope))
            ) or 0

            if is_super:
                new_partnerships_count = (
                    await session.scalar(
                        select(func.count(PartnershipRequest.id)).where(
                            PartnershipRequest.status == "new"
                        )
                    )
                ) or 0

            # Загружаем конкретные элементы для выпадающего списка уведомлений
            stmt = (
                select(Ticket)
                .options(selectinload(Ticket.department), selectinload(Ticket.user))
                .where(*base_scope)
                .order_by(Ticket.updated_at.desc(), Ticket.created_at.desc())
                .limit(10)
            )
            tickets = list((await session.scalars(stmt)).all())

            for t in tickets:
                is_reply = bool(t.response_text and t.response_text.strip())
                dept_name = t.department.name if t.department else "Общий отдел"
                preview = (t.description or "").strip()
                if len(preview) > 90:
                    preview = preview[:90] + "..."
                created_str = (
                    t.created_at.strftime("%d.%m %H:%M") if t.created_at else ""
                )

                if is_reply:
                    items.append({
                        "id": t.id,
                        "type": "student_reply",
                        "type_label": "Ответ студента",
                        "title": f"Ответ по заявке #{t.id}",
                        "icon": "💬",
                        "badge_class": "badge-reply",
                        "department": dept_name,
                        "text": preview or "Студент направил дополнение к заявке",
                        "time": created_str,
                        "url": f"/tickets/?open={t.id}",
                    })
                else:
                    items.append({
                        "id": t.id,
                        "type": "new_ticket",
                        "type_label": "Новая заявка",
                        "title": f"Заявка #{t.id}",
                        "icon": "🔥",
                        "badge_class": "badge-ticket",
                        "department": dept_name,
                        "text": preview or (t.topic or "Новое обращение"),
                        "time": created_str,
                        "url": f"/tickets/?open={t.id}",
                    })

            if is_super:
                pstmt = (
                    select(PartnershipRequest)
                    .where(PartnershipRequest.status == "new")
                    .order_by(PartnershipRequest.created_at.desc())
                    .limit(5)
                )
                partnerships = list((await session.scalars(pstmt)).all())
                for p in partnerships:
                    p_text = (p.proposal_text or "").strip()
                    if len(p_text) > 90:
                        p_text = p_text[:90] + "..."
                    p_time = p.created_at.strftime("%d.%m %H:%M") if p.created_at else ""
                    partner_title = p.user_name or p.contact_info or f"Заявка #{p.id}"
                    items.append({
                        "id": p.id,
                        "type": "partnership",
                        "type_label": "Партнёрство",
                        "title": f"Партнёрство: {partner_title}",
                        "icon": "🤝",
                        "badge_class": "badge-partner",
                        "department": "Партнёрство",
                        "text": p_text or "Новое партнёрское предложение",
                        "time": p_time,
                        "url": "/partnerships/",
                    })

    except Exception:
        logger.exception("API: не удалось получить счетчики")
        return api_error("Ошибка получения счетчиков", 500)

    total = new_tickets_count + student_replies_count + new_partnerships_count

    first_new_ticket = next((i for i in items if i["type"] == "new_ticket"), None)
    first_reply = next((i for i in items if i["type"] == "student_reply"), None)

    return api_success({
        "total": total,
        "total_notifications": total,
        "new_tickets": new_tickets_count,
        "new_tickets_count": new_tickets_count,
        "student_replies": student_replies_count,
        "student_replies_count": student_replies_count,
        "new_partnerships": new_partnerships_count,
        "new_partnerships_count": new_partnerships_count,
        "new_ticket_direct_url": first_new_ticket["url"] if (new_tickets_count == 1 and first_new_ticket) else "/tickets/?status=new",
        "student_reply_direct_url": first_reply["url"] if (student_replies_count == 1 and first_reply) else "/tickets/?status=new",
        "partnership_direct_url": "/partnerships/",
        "items": items,
    })
