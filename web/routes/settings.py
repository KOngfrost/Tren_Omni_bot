"""Маршруты настроек пользовательского интерфейса и параметров профиля."""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from core import PROJECT_VERSION
from core.two_factor import is_two_factor_enabled, set_two_factor_mode
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
    two_factor_on = await is_two_factor_enabled()

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "user": user,
            "active": "settings",
            "csrf_token": get_csrf_token(request),
            "current_theme": saved_theme,
            "glass_effect": glass_effect,
            "two_factor_enabled": two_factor_on,
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


@router.post("/2fa")
async def toggle_2fa(
    request: Request,
    user: dict = Depends(require_auth),
    enabled: str | None = Form(None),
):
    """Включить или отключить двухфакторную аутентификацию (2FA) для панели.

    Доступно только суперадминистраторам (SUPERADMIN).
    """
    if str(user.get("role", "")).upper() != "SUPERADMIN":
        raise HTTPException(
            status_code=403,
            detail="Доступ запрещён: требуется роль суперадминистратора для изменения 2FA.",
        )

    is_enabled = enabled in ("true", "1", "on")
    result = await set_two_factor_mode(is_enabled, updated_by=user.get("username", "admin"))

    accept = request.headers.get("accept", "")
    is_ajax = "application/json" in accept or request.headers.get("x-requested-with") == "XMLHttpRequest"
    if is_ajax:
        return JSONResponse(
            {
                "success": True,
                "enabled": is_enabled,
                "updated_at": result.get("updated_at"),
            }
        )

    status_msg = "включена" if is_enabled else "отключена"
    request.session["flash_success"] = f"Двухфакторная аутентификация (2FA) успешно {status_msg}."
    return RedirectResponse(url="/settings/", status_code=303)

