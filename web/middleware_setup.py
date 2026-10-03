"""Регистрация middleware и middleware-функции веб-панели."""

import logging
import time
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from core.audit import AuditMiddleware
from core.cache import cache_get, cache_set
from core.config import settings
from core.database import async_session_maker
from web.security.csrf import CSRFMiddleware
from web.security.middleware import (
    MaintenanceMiddleware,
    RequestSizeLimitMiddleware,
    RequestSizeValidator,
    SecurityHeadersMiddleware,
)
from web.security.session_store import RedisSessionMiddleware

logger = logging.getLogger(__name__)
REQUEST_MAX_BODY_SIZE = 15 * 1024 * 1024
SKIP_MIDDLEWARE_PREFIXES = (
    "/static/",
    "/health",
    "/auth/",
    "/legal/",
    "/favicon.ico",
    "/maintenance",
)
_request_size_validator = RequestSizeValidator(max_body_size=REQUEST_MAX_BODY_SIZE)


async def add_static_cache_headers(request: Request, call_next):
    """Добавляет Cache-Control заголовок для статики."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=86400, immutable"
    return response


async def validate_request_size(request: Request, call_next):
    """Проверяет объявленный размер запроса до его обработки."""
    await _request_size_validator.check_form_size(request)
    return await call_next(request)


async def _populate_notification_counts(
    request: Request, user: dict, web_user_id: int | None
) -> None:
    from web.dependencies import get_admin_scope

    request.state.new_tickets_count = 0
    request.state.student_replies_count = 0
    request.state.new_partnerships_count = 0
    request.state.total_notifications_count = 0
    try:
        counts_cache_key = f"notif_counts:{web_user_id or user.get('username')}"
        cached_counts = await cache_get(counts_cache_key)
        if cached_counts is not None:
            request.state.new_tickets_count = cached_counts.get("new_tickets_count", 0)
            request.state.student_replies_count = cached_counts.get("student_replies_count", 0)
            request.state.new_partnerships_count = cached_counts.get("new_partnerships_count", 0)
            request.state.total_notifications_count = cached_counts.get(
                "total_notifications_count", 0
            )
            return

        from sqlalchemy import and_, func, or_, select

        from core.models import PartnershipRequest, Ticket, TicketStatus

        async with async_session_maker() as count_session:
            is_super_count, user_department_id = await get_admin_scope(count_session, user)
            ticket_scope = [Ticket.status == TicketStatus.NEW]
            if not is_super_count:
                if user_department_id:
                    ticket_scope.append(
                        or_(
                            Ticket.department_id == user_department_id,
                            Ticket.department_id.is_(None),
                        )
                    )
                else:
                    ticket_scope.append(Ticket.department_id.is_(None))

            new_ticket_scope = [
                *ticket_scope,
                or_(Ticket.response_text.is_(None), Ticket.response_text == ""),
            ]
            request.state.new_tickets_count = (
                await count_session.scalar(select(func.count(Ticket.id)).where(*new_ticket_scope))
            ) or 0

            reply_scope = [
                *ticket_scope,
                and_(Ticket.response_text.is_not(None), Ticket.response_text != ""),
            ]
            request.state.student_replies_count = (
                await count_session.scalar(select(func.count(Ticket.id)).where(*reply_scope))
            ) or 0

            if is_super_count:
                request.state.new_partnerships_count = (
                    await count_session.scalar(
                        select(func.count(PartnershipRequest.id)).where(
                            PartnershipRequest.status == "new"
                        )
                    )
                ) or 0

            request.state.total_notifications_count = (
                request.state.new_tickets_count
                + request.state.student_replies_count
                + request.state.new_partnerships_count
            )
            await cache_set(
                counts_cache_key,
                {
                    "new_tickets_count": request.state.new_tickets_count,
                    "student_replies_count": request.state.student_replies_count,
                    "new_partnerships_count": request.state.new_partnerships_count,
                    "total_notifications_count": request.state.total_notifications_count,
                },
                ttl=20,
            )
    except Exception:
        logger.debug("Не удалось загрузить счетчики для middleware", exc_info=True)


async def add_department_name(request: Request, call_next):
    """Загружает department_name и список отделов пользователя из БД/кэша."""
    session = request.scope.get("session", {})
    request.state.session_id = session.get("session_id")
    credentials = session.get("created_credentials")
    if isinstance(credentials, dict) and time.time() - credentials.get("created_at", 0) > 300:
        session.pop("created_credentials", None)

    if any(
        request.url.path.startswith(prefix) for prefix in SKIP_MIDDLEWARE_PREFIXES
    ) or "application/json" in request.headers.get("accept", ""):
        return await call_next(request)

    try:
        user = session.get("user")
        if user:
            from web.dependencies import get_admin_scope, get_departments_for_user

            web_user_id = user.get("web_user_id")
            cached = await cache_get(f"dept_ctx:{web_user_id}") if web_user_id else None
            if cached is not None:
                department_name = cached.get("department_name")
                user_departments = [
                    SimpleNamespace(id=department["id"], name=department["name"])
                    for department in cached.get("departments", [])
                ]
            else:
                async with async_session_maker() as db_session:
                    user_departments = await get_departments_for_user(db_session, user)
                    is_super, _department_id = await get_admin_scope(db_session, user)

                department_name = (
                    user_departments[0].name if not is_super and user_departments else None
                )
                if web_user_id:
                    await cache_set(
                        f"dept_ctx:{web_user_id}",
                        {
                            "department_name": department_name,
                            "departments": [
                                {"id": department.id, "name": department.name}
                                for department in user_departments
                            ],
                        },
                        ttl=300,
                    )

            department_id = None
            if request.url.path.startswith("/dept/"):
                parts = request.url.path.strip("/").split("/")
                if len(parts) >= 2 and parts[1].isdigit():
                    department_id = int(parts[1])

            request.state.department_name = department_name
            request.state.user_departments = user_departments
            request.state.dept_id = department_id
            await _populate_notification_counts(request, user, web_user_id)
    except Exception:
        logger.debug("Не удалось загрузить department_name для middleware", exc_info=True)

    return await call_next(request)


def setup_middleware(app: FastAPI) -> None:
    """Зарегистрировать middleware в порядке, требуемом Starlette."""
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        instrumentator = Instrumentator(
            should_group_status_codes=False,
            excluded_handlers=[".*admin/health", "/health", "/metrics"],
        )
        instrumentator.instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    except Exception as exc:
        logger.warning("Не удалось настроить Prometheus Instrumentator: %s", exc)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "X-CSRF-Token",
            "Authorization",
            "Accept",
            "X-Requested-With",
        ],
    )
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(MaintenanceMiddleware)

    session_secret = settings.session_secret_key
    if not session_secret:
        raise RuntimeError(
            "SESSION_SECRET_KEY не задан. Установите его в .env и перезапустите панель. "
            'Генерация: python -c "import secrets; print(secrets.token_urlsafe(64))"'
        )
    settings.ensure_production_config()
    app.add_middleware(
        RedisSessionMiddleware,
        secret_key=session_secret,
        max_age=settings.SESSION_TTL,
        https_only=settings.SESSION_HTTPS_ONLY,
        same_site=settings.SESSION_SAME_SITE,
        path="/",
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(RequestSizeLimitMiddleware, max_body_size=REQUEST_MAX_BODY_SIZE)
    app.add_middleware(AuditMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    app.middleware("http")(add_static_cache_headers)
    app.middleware("http")(validate_request_size)
    app.middleware("http")(add_department_name)
