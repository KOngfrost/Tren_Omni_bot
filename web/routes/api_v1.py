"""Версионированное REST API v1 для интеграций и SPA.

Предоставляет:
- Строгую Pydantic-валидацию входных данных и схем ответов
- OpenAPI/Swagger спецификацию на уровне эндпоинтов
- Кэширование аналитических метрик через Redis (с TTL)
- Полную обратную совместимость
"""

import hashlib
import hmac
import json
import logging
import re
import secrets
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from core.cache import cache_delete, cache_get, cache_set
from core.config import settings
from core.database import async_session_maker, get_db
from core.models import Department, Log, Ticket, TicketStatus, WebRole, WebUser
from core.redis_client import is_redis_available
from core.two_factor import is_two_factor_enabled
from web.dependencies import get_admin_scope, require_auth, require_superadmin
from web.routes.api import _get_all_departments_usage, _get_department_usage
from web.routes.auth import (
    _authenticate,
    _clear_attempts,
    _get_client_ip,
    _is_rate_limited,
    _log_action,
    _mask_vk_id,
    _notify_superadmin,
    _record_failed_attempt,
    _send_otp_to_vk,
    require_crud_rate_limit,
)
from web.schemas import (
    MAX_DEPARTMENT_NAME_LEN,
    DepartmentNamePayload,
    DepartmentSchema,
    DepartmentUsageSchema,
    SystemStatsResponse,
    TicketSummarySchema,
)
from web.security import otp_store
from web.security.csrf import get_csrf_token
from web.security.middleware import sanitize_html

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["API v1"])


class LoginRequest(BaseModel):
    username: str
    password: str


class Verify2FARequest(BaseModel):
    code: str


class TelegramAuthRequest(BaseModel):
    init_data: str


def _validate_telegram_init_data(init_data: str, bot_token: str) -> dict[str, str] | None:
    """Проверить HMAC-SHA256 подпись Telegram WebApp initData."""
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
        if "hash" not in parsed:
            return None
        received_hash = parsed.pop("hash")
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not secrets.compare_digest(received_hash, computed_hash):
            return None
        return parsed
    except Exception:
        return None


# === Аутентификация API ===


@router.get(
    "/auth/me",
    summary="Получить текущего пользователя сессии",
)
async def v1_auth_me(request: Request) -> dict[str, Any]:
    """Вернуть данные текущего пользователя и CSRF токен."""
    user = request.session.get("user")
    token = get_csrf_token(request)
    return {
        "authenticated": bool(user),
        "user": user,
        "csrf_token": token,
    }


@router.post(
    "/auth/login",
    summary="Авторизация через JSON",
)
async def v1_auth_login(payload: LoginRequest, request: Request) -> dict[str, Any]:
    """Вход администратора по логину и паролю."""
    client_ip = _get_client_ip(request)
    username = payload.username.strip()
    password = payload.password

    async with async_session_maker() as session:
        if await _is_rate_limited(session, client_ip):
            details = f"Блокировка IP {client_ip}: превышен лимит попыток входа (username={username!r})"
            await _log_action("web_login_blocked", details)
            await _notify_superadmin(details)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Слишком много попыток входа. Подождите 15 минут.",
            )

        user_data = await _authenticate(session, username, password)
        if user_data is None:
            await _record_failed_attempt(client_ip)
            await _log_action("web_login_failed", f"Неудачный вход с IP {client_ip} (username={username!r})")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный логин или пароль",
            )

        if user_data.get("needs_2fa"):
            vk_admin_id = user_data.get("vk_admin_id")
            if not vk_admin_id:
                await otp_store.cancel(request)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Включена двухфакторная аутентификация, но доверенный канал не настроен.",
                )

            otp_code = await otp_store.begin(
                request,
                user_data={
                    "username": user_data["username"],
                    "role": user_data["role"],
                    "web_user_id": user_data["web_user_id"],
                    "department_id": user_data["department_id"],
                },
                vk_admin_id=vk_admin_id,
                ttl=settings.TWO_FACTOR_CODE_TTL,
            )
            await _send_otp_to_vk(
                session,
                vk_admin_id=int(vk_admin_id),
                otp_code=otp_code,
                reason="Одноразовый код для входа в панель управления OSS Bot",
            )
            return {
                "success": True,
                "needs_2fa": True,
                "masked_vk_id": _mask_vk_id(vk_admin_id),
            }

        await _clear_attempts(client_ip)
        request.session["user"] = user_data
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    await _log_action(
        "web_login_success",
        f"Успешный вход {user_data['username']} (роль {user_data['role']}) с IP {client_ip}",
    )
    return {
        "success": True,
        "needs_2fa": False,
        "user": user_data,
        "csrf_token": request.session["csrf_token"],
    }


@router.post(
    "/auth/2fa",
    summary="Подтверждение 2FA кода",
)
async def v1_auth_verify_2fa(payload: Verify2FARequest, request: Request) -> dict[str, Any]:
    """Верификация одноразового кода 2FA."""
    code = re.sub(r"\D", "", payload.code)[:6]
    pending = await otp_store.peek(request)
    if not pending:
        await otp_store.cancel(request)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Сессия 2FA истекла")

    result = await otp_store.verify(request, code)
    if result.status == "expired":
        await otp_store.cancel(request)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Срок действия кода истёк")

    if not result.ok:
        if result.status == "locked":
            await otp_store.cancel(request)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Превышено число попыток ввода 2FA")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Неверный код. Осталось попыток: {result.remaining}",
        )

    user_data = dict(result.user_data or {})
    async with async_session_maker() as session:
        if user_data.get("web_user_id") is not None:
            web_user = await session.get(WebUser, user_data["web_user_id"])
            if not web_user or not web_user.is_active:
                await otp_store.cancel(request)
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Учётная запись не активна")
            user_data["role"] = web_user.role.value if web_user.role else WebRole.DEPARTMENT_ADMIN.value

    await otp_store.cancel(request)
    request.session["user"] = user_data
    request.session["csrf_token"] = secrets.token_urlsafe(32)
    client_ip = _get_client_ip(request)
    await _clear_attempts(client_ip)
    await _log_action(
        "web_login_2fa_success",
        f"Успешный 2FA-вход {user_data['username']} (роль {user_data['role']}) с IP {client_ip}",
    )
    return {
        "success": True,
        "user": user_data,
        "csrf_token": request.session["csrf_token"],
    }


@router.post(
    "/auth/telegram-webapp",
    summary="Авторизация через Telegram Mini App",
)
async def v1_auth_telegram_webapp(payload: TelegramAuthRequest, request: Request) -> dict[str, Any]:
    """Бесшовный вход для Telegram-администратора через Telegram.WebApp.initData."""
    if not settings.TELEGRAM_BOT_TOKEN or settings.TELEGRAM_ADMIN_ID <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Telegram интеграция не настроена")

    validated = _validate_telegram_init_data(payload.init_data, settings.TELEGRAM_BOT_TOKEN)
    if not validated:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Недействительная подпись Telegram")

    try:
        user_raw = json.loads(validated.get("user", "{}"))
        tg_user_id = int(user_raw.get("id", 0))
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверные данные пользователя Telegram")

    if tg_user_id != settings.TELEGRAM_ADMIN_ID:
        logger.warning("Попытка входа через WebApp с недоверенного Telegram ID=%s", tg_user_id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ запрещён для данного Telegram аккаунта")

    # Авторизуем как главного администратора
    user_data = {
        "username": f"tg_{tg_user_id}",
        "role": WebRole.SUPERADMIN.value,
        "web_user_id": None,
        "bootstrap": True,
        "department_id": None,
        "telegram_id": tg_user_id,
    }
    request.session["user"] = user_data
    request.session["csrf_token"] = secrets.token_urlsafe(32)
    client_ip = _get_client_ip(request)
    await _log_action("web_login_tg_success", f"Вход через Telegram WebApp ID={tg_user_id} с IP {client_ip}")

    return {
        "success": True,
        "user": user_data,
        "csrf_token": request.session["csrf_token"],
    }


@router.post(
    "/auth/logout",
    summary="Выход из системы",
)
async def v1_auth_logout(request: Request) -> dict[str, Any]:
    """Сброс сессии пользователя."""
    request.session.clear()
    return {"success": True}



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
    two_factor_on = await is_two_factor_enabled()
    stats = SystemStatsResponse(
        version=settings.PROJECT_VERSION,
        environment=settings.APP_ENV,
        redis_connected=redis_ok,
        sentry_enabled=bool(settings.SENTRY_DSN),
        two_factor_enabled=two_factor_on,
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
