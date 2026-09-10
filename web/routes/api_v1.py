"""Версионированное REST API v1 для интеграций и SPA.

Предоставляет:
- Строгую Pydantic-валидацию входных данных и схем ответов
- OpenAPI/Swagger спецификацию на уровне эндпоинтов
- Кэширование аналитических метрик через Redis (с TTL)
- Полную обратную совместимость
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from core.cache import cache_delete, cache_get, cache_set
from core.config import settings
from core.database import async_session_maker
from core.models import Department, Ticket, TicketStatus
from core.redis_client import is_redis_available
from web.dependencies import get_admin_scope, require_auth, require_superadmin
from web.routes.api import _get_all_departments_usage, _get_department_usage
from web.routes.auth import require_crud_rate_limit
from web.schemas import (
    MAX_DEPARTMENT_NAME_LEN,
    DepartmentNamePayload,
    DepartmentSchema,
    DepartmentUsageSchema,
    SystemStatsResponse,
    TicketSummarySchema,
)
from web.security.middleware import sanitize_html

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["API v1"])


@router.get(
    "/departments/",
    response_model=list[DepartmentSchema],
    summary="Получить список отделов с аналитикой",
)
async def v1_get_departments(user=Depends(require_auth)) -> list[DepartmentSchema]:
    """Получить список доступных отделов со статистикой использования."""
    async with async_session_maker() as session:
        is_super, dept_id = await get_admin_scope(session, user)
        stmt = select(Department).order_by(Department.name)
        if not is_super:
            stmt = stmt.where(Department.id == dept_id)

        departments = list((await session.execute(stmt)).scalars().all())
        dept_ids = [d.id for d in departments]
        usage_map = await _get_all_departments_usage(session, dept_ids)

        return [
            DepartmentSchema(
                id=d.id,
                name=d.name or "",
                created_at=getattr(d, "created_at", None),
                usage=DepartmentUsageSchema(**usage_map.get(d.id, {})),
            )
            for d in departments
        ]


@router.get(
    "/departments/{dept_id}",
    response_model=DepartmentSchema,
    summary="Получить информацию об отделе",
)
async def v1_get_department(dept_id: int, user=Depends(require_superadmin)) -> DepartmentSchema:
    """Получить данные конкретного отдела по его ID."""
    async with async_session_maker() as session:
        dept = await session.get(Department, dept_id)
        if not dept:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Отдел не найден")
        usage = await _get_department_usage(session, dept_id)
        return DepartmentSchema(
            id=dept.id,
            name=dept.name or "",
            created_at=getattr(dept, "created_at", None),
            usage=DepartmentUsageSchema(**usage),
        )


@router.post(
    "/departments/",
    response_model=DepartmentSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Создать новый отдел",
)
async def v1_create_department(
    payload: DepartmentNamePayload,
    request: Request,
    user=Depends(require_superadmin),
) -> DepartmentSchema:
    """Создать новый отдел в системе."""
    await require_crud_rate_limit(request)
    name = sanitize_html(payload.name).strip()

    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Название отдела не может быть пустым"
        )
    if len(name) > MAX_DEPARTMENT_NAME_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Название длиннее {MAX_DEPARTMENT_NAME_LEN} символов",
        )

    async with async_session_maker() as session:
        exists = await session.scalar(
            select(func.count(Department.id)).where(Department.name == name)
        )
        if exists:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Отдел с таким названием уже существует",
            )

        dept = Department(name=name)
        session.add(dept)
        await session.commit()
        await session.refresh(dept)

        # Инвалидируем кэш отделов
        await cache_delete("v1_all_departments")
        logger.info("API v1: создан отдел id=%s, name=%s", dept.id, dept.name)
        return DepartmentSchema(
            id=dept.id,
            name=dept.name,
            created_at=getattr(dept, "created_at", None),
            usage=DepartmentUsageSchema(),
        )


@router.put(
    "/departments/{dept_id}",
    response_model=DepartmentSchema,
    summary="Переименовать отдел",
)
async def v1_rename_department(
    dept_id: int,
    payload: DepartmentNamePayload,
    request: Request,
    user=Depends(require_superadmin),
) -> DepartmentSchema:
    """Обновить название существующего отдела."""
    await require_crud_rate_limit(request)
    name = sanitize_html(payload.name).strip()

    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Название отдела не может быть пустым"
        )
    if len(name) > MAX_DEPARTMENT_NAME_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Название длиннее {MAX_DEPARTMENT_NAME_LEN} символов",
        )

    async with async_session_maker() as session:
        dept = await session.get(Department, dept_id)
        if not dept:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Отдел не найден")

        exists = await session.scalar(
            select(func.count(Department.id)).where(
                Department.name == name, Department.id != dept_id
            )
        )
        if exists:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Отдел с таким названием уже существует",
            )

        dept.name = name
        await session.commit()
        usage = await _get_department_usage(session, dept_id)

        await cache_delete("v1_all_departments")
        logger.info("API v1: отдел id=%s переименован в '%s'", dept_id, name)
        return DepartmentSchema(
            id=dept.id,
            name=dept.name,
            created_at=getattr(dept, "created_at", None),
            usage=DepartmentUsageSchema(**usage),
        )


@router.delete(
    "/departments/{dept_id}",
    summary="Удалить отдел",
)
async def v1_delete_department(
    dept_id: int,
    request: Request,
    user=Depends(require_superadmin),
) -> dict[str, Any]:
    """Удалить отдел (только если в нём нет связанных сущностей)."""
    await require_crud_rate_limit(request)
    async with async_session_maker() as session:
        dept = await session.get(Department, dept_id)
        if not dept:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Отдел не найден")

        usage = await _get_department_usage(session, dept_id)
        if any(usage.values()):
            parts = ", ".join(f"{k}: {v}" for k, v in usage.items() if v)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Отдел «{dept.name}» не пуст ({parts}). Сначала удалите связанные данные.",
            )

        await session.delete(dept)
        await session.commit()
        await cache_delete("v1_all_departments")
        logger.info("API v1: отдел id=%s удалён", dept_id)
        return {"id": dept_id, "deleted": True}


@router.get(
    "/stats",
    response_model=SystemStatsResponse,
    summary="Общая статистика системы",
)
async def v1_get_stats(user=Depends(require_auth)) -> SystemStatsResponse:
    """Получить системные метрики и статистику заявок (с кэшированием TTL)."""
    cached = await cache_get("v1_system_stats")
    if cached:
        return SystemStatsResponse(**cached)

    async with async_session_maker() as session:
        total = await session.scalar(select(func.count(Ticket.id))) or 0
        active = (
            await session.scalar(
                select(func.count(Ticket.id)).where(
                    Ticket.status.in_([TicketStatus.NEW, TicketStatus.IN_PROGRESS])
                )
            )
            or 0
        )

    redis_ok = await is_redis_available()
    stats = SystemStatsResponse(
        version=settings.PROJECT_VERSION,
        environment=settings.APP_ENV,
        redis_connected=redis_ok,
        sentry_enabled=bool(settings.SENTRY_DSN),
        two_factor_enabled=settings.TWO_FACTOR_ENABLED,
        total_tickets=total,
        active_tickets=active,
    )
    await cache_set("v1_system_stats", stats.model_dump(), ttl=60)
    return stats


@router.get(
    "/tickets/",
    response_model=list[TicketSummarySchema],
    summary="Список последних заявок",
)
async def v1_get_tickets(
    limit: int = 50,
    user=Depends(require_auth),
) -> list[TicketSummarySchema]:
    """Получить список последних заявок."""
    limit = min(max(1, limit), 100)
    async with async_session_maker() as session:
        is_super, dept_id = await get_admin_scope(session, user)
        stmt = (
            select(Ticket)
            .options(selectinload(Ticket.department))
            .order_by(Ticket.created_at.desc())
            .limit(limit)
        )
        if not is_super:
            stmt = stmt.where(Ticket.department_id == dept_id)

        tickets = list((await session.execute(stmt)).scalars().all())
        return [
            TicketSummarySchema(
                id=t.id,
                topic=t.topic or "",
                status=t.status.value if t.status else "",
                department_id=t.department_id,
                department_name=t.department.name if t.department else None,
                created_at=t.created_at.isoformat() if t.created_at else None,
            )
            for t in tickets
        ]
