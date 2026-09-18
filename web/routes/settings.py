"""Маршруты настроек пользовательского интерфейса и параметров профиля."""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from core import PROJECT_VERSION
from core.config import settings
from web.dependencies import require_auth
from web.security.csrf import get_csrf_token
from web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
@router.get("", response_class=HTMLResponse)
async def settings_page(request: Request, user: dict = Depends(require_auth)):
    """Страница настроек интерфейса и профиля администратора."""
    saved_theme = request.cookies.get("app_theme", "dark")
    if saved_theme not in ("dark", "light", "system"):
        saved_theme = "dark"

    glass_effect = request.cookies.get("app_glass_effect", "true") != "false"

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "user": user,
            "active": "settings",
            "csrf_token": get_csrf_token(request),
            "current_theme": saved_theme,
            "glass_effect": glass_effect,
            "two_factor_enabled": settings.TWO_FACTOR_ENABLED,
            "version": PROJECT_VERSION,
        },
    )


@router.post("/theme")
async def update_theme(
    request: Request,
    user: dict = Depends(require_auth),
    theme: Literal["dark", "light", "system"] = Form("dark"),
    glass_effect: str | None = Form(None),
):
    """Сохранить предпочтения темы и визуальных эффектов."""
    glass_enabled = glass_effect in ("true", "1", "on")

    accept = request.headers.get("accept", "")
    is_ajax = "application/json" in accept or request.headers.get("x-requested-with") == "XMLHttpRequest"

    if is_ajax:
        response = JSONResponse(
            {
                "success": True,
                "theme": theme,
                "glass_effect": glass_enabled,
            }
        )
    else:
        request.session["flash_success"] = "Настройки оформления успешно сохранены."
        response = RedirectResponse(url="/settings/", status_code=303)

    response.set_cookie(
        key="app_theme",
        value=theme,
        max_age=31536000,
        path="/",
        samesite="lax",
        secure=False,
        httponly=False,
    )
    response.set_cookie(
        key="app_glass_effect",
        value="true" if glass_enabled else "false",
        max_age=31536000,
        path="/",
        samesite="lax",
        secure=False,
        httponly=False,
    )
    return response
