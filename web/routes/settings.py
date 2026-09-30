"""Маршруты настроек пользовательского интерфейса и параметров профиля."""

import logging
import re
from typing import Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from core import PROJECT_VERSION
from core.config import settings
from core.two_factor import is_two_factor_enabled, set_two_factor_mode
from web.dependencies import require_auth
from web.security.csrf import get_csrf_token
from web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()

# Брендовый цвет по умолчанию: цвет иконки сайта #fd60c9
DEFAULT_ACCENT_COLOR = "#fd60c9"
_ACCENT_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalize_accent_color(raw: str | None) -> str | None:
    """Проверить и нормализовать hex акцентного цвета.

    Возвращает None, если значение некорректно — тогда применяется тема
    по умолчанию, а не «мусор» в CSS.
    """
    if not raw:
        return None
    value = str(raw).strip()
    if not _ACCENT_RE.match(value):
        return None
    # Приводим к полному виду #RRGGBB: короткий #RGB разворачиваем.
    if len(value) == 4:
        value = "#" + "".join(ch * 2 for ch in value[1:])
    return value.lower()


@router.get("/", response_class=HTMLResponse)
@router.get("", response_class=HTMLResponse)
async def settings_page(request: Request, user: dict = Depends(require_auth)):
    """Страница настроек интерфейса и профиля администратора."""
    saved_theme = request.cookies.get("app_theme", "dark")
    if saved_theme not in ("dark", "light", "system"):
        saved_theme = "dark"

    glass_effect = request.cookies.get("app_glass_effect", "true") != "false"
    accent_color = normalize_accent_color(request.cookies.get("app_accent_color")) or (
        DEFAULT_ACCENT_COLOR
    )
    two_factor_global = await is_two_factor_enabled()

    # Персональное состояние 2FA читаем из БД — источник истины, а не сессия.
    two_factor_personal = bool(user.get("two_factor_enabled", True))
    two_factor_available = bool(user.get("two_factor_available", False))

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "user": user,
            "active": "settings",
            "csrf_token": get_csrf_token(request),
            "current_theme": saved_theme,
            "glass_effect": glass_effect,
            "accent_color": accent_color,
            "two_factor_enabled": two_factor_global,
            "two_factor_personal": two_factor_personal,
            "two_factor_available": two_factor_available,
            "version": PROJECT_VERSION,
        },
    )


@router.post("/theme")
async def update_theme(
    request: Request,
    user: dict = Depends(require_auth),
    theme: Literal["dark", "light", "system"] = Form("dark"),
    glass_effect: str | None = Form(None),
    accent_color: str | None = Form(None),
):
    """Сохранить предпочтения темы, эффектов и акцентного цвета."""
    glass_enabled = glass_effect in ("true", "1", "on")
    # Некорректный hex игнорируем и оставляем текущий/тему по умолчанию.
    accent = normalize_accent_color(accent_color) or normalize_accent_color(
        request.cookies.get("app_accent_color")
    ) or DEFAULT_ACCENT_COLOR

    accept = request.headers.get("accept", "")
    is_ajax = "application/json" in accept or request.headers.get("x-requested-with") == "XMLHttpRequest"

    if is_ajax:
        response = JSONResponse(
            {
                "success": True,
                "theme": theme,
                "glass_effect": glass_enabled,
                "accent_color": accent,
            }
        )
    else:
        request.session["flash_success"] = "Настройки оформления успешно сохранены."
        response = RedirectResponse(url="/settings/", status_code=303)

    cookie_secure = bool(settings.SESSION_HTTPS_ONLY or settings.IS_PRODUCTION)
    response.set_cookie(
        key="app_theme",
        value=theme,
        max_age=31536000,
        path="/",
        samesite="lax",
        secure=cookie_secure,
        httponly=False,
    )
    response.set_cookie(
        key="app_glass_effect",
        value="true" if glass_enabled else "false",
        max_age=31536000,
        path="/",
        samesite="lax",
        secure=cookie_secure,
        httponly=False,
    )
    response.set_cookie(
        key="app_accent_color",
        value=accent,
        max_age=31536000,
        path="/",
        samesite="lax",
        secure=cookie_secure,
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

    Доступно только суперадминистраторам (SUPERADMIN). Это МАСТЕР-переключатель:
    при его выключении 2FA не применяется ни к одному аккаунту, независимо от
    личных настроек.
    """
    if str(user.get("role", "")).upper() != "SUPERADMIN" or user.get("is_temporary"):
        raise HTTPException(
            status_code=403,
            detail="Доступ запрещён: требуется постоянная роль суперадминистратора для изменения 2FA.",
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


@router.post("/2fa/personal")
async def toggle_personal_2fa(
    request: Request,
    user: dict = Depends(require_auth),
    enabled: str | None = Form(None),
):
    """Включить или отключить 2FA для СВОЕГО аккаунта.

    Правила:
    - каждый администратор с привязкой к VK решает сам, нужен ли ему второй
      фактор; глобальный переключатель при этом остаётся мастер-режимом;
    - без привязки к VK 2FA не применяется (код некуда доставить), поэтому
      для временных / QA-учётных записей переключатель недоступен.
    """
    if not user.get("web_user_id"):
        raise HTTPException(status_code=403, detail="Требуется постоянная учётная запись.")

    web_user_id = int(user["web_user_id"])
    want_enabled = enabled in ("true", "1", "on")

    # core.database читаем в момент вызова, а не импортом: иначе подмена
    # соединения в тестах не применится к этому обработчику.
    from core import database
    from core.models import WebUser

    session = database.async_session_maker()
    async with session:
        web_user = await session.get(WebUser, web_user_id)
        if web_user is None:
            raise HTTPException(status_code=404, detail="Учётная запись не найдена.")

        if not web_user.two_factor_available:
            request.session["flash_error"] = (
                "2FA недоступна: учётная запись не привязана к VK. "
                "Код подтверждения некуда доставить."
            )
            return RedirectResponse(url="/settings/", status_code=303)

        web_user.two_factor_enabled = want_enabled
        await session.commit()

    # Обновляем сессию, чтобы состояние совпадало с БД без перелогина.
    session_user = request.session.get("user")
    if isinstance(session_user, dict):
        session_user["two_factor_enabled"] = want_enabled

    action = "Включена" if want_enabled else "Отключена"
    request.session["flash_success"] = f"2FA {action.lower()} для вашего аккаунта."
    return RedirectResponse(url="/settings/", status_code=303)

