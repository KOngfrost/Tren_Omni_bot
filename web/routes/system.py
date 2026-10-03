"""Системные endpoints healthcheck и статических уведомлений."""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from core.config import settings
from web.dependencies import require_auth, require_superadmin
from web.templating import templates

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/docs", include_in_schema=False)
async def authenticated_swagger_docs(request: Request, user: dict = Depends(require_auth)):
    """Swagger UI; schema URL independently requires an authenticated session."""
    return get_swagger_ui_html(
        openapi_url="/api/openapi.json",
        title=f"{request.app.title} - Swagger UI",
    )


@router.get("/api/redoc", include_in_schema=False)
async def authenticated_redoc_docs(request: Request, user: dict = Depends(require_auth)):
    """ReDoc UI for authenticated administrators."""
    return get_redoc_html(
        openapi_url="/api/openapi.json",
        title=f"{request.app.title} - ReDoc",
    )


@router.get("/api/openapi.json", include_in_schema=False)
async def authenticated_openapi_schema(request: Request, user: dict = Depends(require_auth)):
    """OpenAPI schema, protected independently from the documentation UIs."""
    return JSONResponse(request.app.openapi())


@router.get("/favicon.ico", include_in_schema=False)
async def favicon_endpoint():
    """Отдача иконки favicon.ico."""
    return FileResponse("web/static/favicon.ico", media_type="image/x-icon")


@router.get("/maintenance", response_class=HTMLResponse, include_in_schema=False)
async def maintenance_endpoint(request: Request):
    """Страница уведомления о проведении технических работ."""
    from core.maintenance import get_maintenance_info, is_maintenance_mode

    info = await get_maintenance_info()
    active = await is_maintenance_mode()
    return templates.TemplateResponse(
        "maintenance.html",
        {
            "request": request,
            "maintenance_active": active,
            "maintenance_message": info.get("message", ""),
            "vk_bot_url": getattr(settings, "vk_bot_url", "https://vk.com"),
            "app_version": settings.APP_VERSION,
        },
    )


@router.get("/health")
async def health():
    """Проверить БД, синхронизацию времени и состояние bootstrap-доступа."""
    from sqlalchemy import text

    from core.bootstrap_guard import bootstrap_mode_status
    from core.database import async_session_maker, engine
    from core.time_utils import check_time_sync

    if engine is None:
        return JSONResponse(
            content={"status": "error", "detail": "database engine not configured"},
            status_code=503,
        )

    try:
        async with engine.begin() as connection:
            await connection.execute(text("SELECT 1"))
            time_sync = await check_time_sync(connection)

        try:
            async with async_session_maker() as session:
                bootstrap = await bootstrap_mode_status(session, settings)
        except Exception as exc:
            logger.warning("Healthcheck: не удалось определить bootstrap-режим: %s", exc)
            bootstrap = None

        safe_bootstrap = None
        if bootstrap is not None:
            safe_bootstrap = dict(bootstrap)
            safe_bootstrap.pop("bootstrap_username", None)

        return {
            "status": "ok",
            "time_sync": time_sync,
            "bootstrap_mode": None if bootstrap is None else bootstrap.get("bootstrap_mode"),
            "bootstrap": safe_bootstrap,
        }
    except Exception:
        logger.exception("Healthcheck: БД недоступна")
        return JSONResponse(
            content={"status": "error", "detail": "database unavailable"},
            status_code=503,
        )


@router.get("/health/detailed")
async def health_detailed(user: dict = Depends(require_superadmin)):
    """Детальная системная диагностика: доступна строго суперадминистраторам."""
    from core.bootstrap_guard import bootstrap_mode_status
    from core.database import async_session_maker
    from core.startup_guard import startup_summary

    async with async_session_maker() as session:
        bootstrap = await bootstrap_mode_status(session, settings)

    return {
        "status": "ok",
        "app_version": settings.APP_VERSION,
        "bootstrap": bootstrap,
        "startup_summary": startup_summary(settings),
    }
