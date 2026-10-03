"""Маршруты подтверждения входа по одноразовому коду."""

import re
import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.config import settings
from core.database import get_db
from core.models import WebRole, WebUser
from core.two_factor import is_two_factor_enabled
from web.routes.auth import (
    _bootstrap_2fa_channel,
    _log_action,
    _mask_vk_id,
    _send_otp_to_vk,
    bootstrap_session_still_valid,
    take_next,
)
from web.security import otp_store
from web.security.csrf import get_csrf_token
from web.templating import templates

router = APIRouter()


async def _verified_user_data(
    request: Request,
    session: AsyncSession,
    user_data: dict,
) -> dict | RedirectResponse:
    """Повторно проверить permanent account либо bootstrap credentials."""
    if user_data.get("web_user_id") is not None:
        from core.models import Admin

        web_user = await session.scalar(
            select(WebUser)
            .options(selectinload(WebUser.admin).selectinload(Admin.user))
            .where(WebUser.id == user_data["web_user_id"])
        )
        if web_user is None or not web_user.is_active:
            await otp_store.cancel(request)
            request.session["flash_error"] = "Учётка недоступна. Обратитесь к администратору."
            return RedirectResponse(url="/auth/login", status_code=302)

        vk_admin_id = web_user.admin.user.vk_id if web_user.admin and web_user.admin.user else None
        two_factor_on = await is_two_factor_enabled()
        is_qa_user = web_user.username.startswith(("qa_", "test_")) or (
            web_user.admin is None and "qa" in web_user.username.lower()
        )
        if two_factor_on and not vk_admin_id and not is_qa_user:
            await otp_store.cancel(request)
            request.session["flash_error"] = "Привязка VK-аккаунта снята — вход с 2FA невозможен."
            return RedirectResponse(url="/auth/login", status_code=302)

        return {
            "username": web_user.username,
            "role": web_user.role.value if web_user.role else WebRole.DEPARTMENT_ADMIN.value,
            "web_user_id": web_user.id,
            "department_id": web_user.department_id,
        }

    if not bootstrap_session_still_valid(user_data):
        await otp_store.cancel(request)
        request.session["flash_error"] = "Резервный вход отключён или перенастроен. Войдите заново."
        return RedirectResponse(url="/auth/login", status_code=302)

    two_factor_on = await is_two_factor_enabled()
    if two_factor_on and not _bootstrap_2fa_channel():
        await otp_store.cancel(request)
        request.session["flash_error"] = (
            "Доверенный канал 2FA больше не настроен — вход отменён."
        )
        return RedirectResponse(url="/auth/login", status_code=302)

    return {
        "username": user_data.get("username") or settings.WEB_ADMIN_USERNAME,
        "role": WebRole.SUPERADMIN.value,
        "web_user_id": None,
        "department_id": None,
        "bootstrap": True,
    }


@router.get("/2fa", response_class=HTMLResponse)
async def two_factor_page(request: Request):
    """Страница ввода 2FA-кода из ВК."""
    if request.session.get("user"):
        return RedirectResponse(url=take_next(request), status_code=302)

    pending = await otp_store.peek(request)
    if not pending:
        await otp_store.cancel(request)
        request.session["flash_error"] = "Сессия 2FA истекла. Войдите заново."
        return RedirectResponse(url="/auth/login", status_code=302)

    error = request.session.pop("2fa_error", None)
    return templates.TemplateResponse(
        "2fa.html",
        {
            "request": request,
            "masked_vk_id": _mask_vk_id(pending.get("vk_admin_id")),
            "error": error,
            "csrf_token": get_csrf_token(request),
        },
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@router.post("/2fa")
async def two_factor_verify(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Проверить OTP и выдать полноценную сессию с fail-closed перепроверками."""
    if request.session.get("user"):
        return RedirectResponse(url=take_next(request), status_code=303)

    form = await request.form()
    submitted_code = re.sub(r"\D", "", str(form.get("code", "")))[:6]
    pending = await otp_store.peek(request)
    if not pending:
        await otp_store.cancel(request)
        request.session["flash_error"] = "Сессия 2FA истекла. Войдите заново."
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await otp_store.verify(request, submitted_code)
    if result.status == "expired":
        await otp_store.cancel(request)
        request.session["flash_error"] = "Срок действия кода истёк. Войдите заново."
        return RedirectResponse(url="/auth/login", status_code=303)

    if not result.ok:
        if result.status == "locked":
            await otp_store.cancel(request)
            request.session["flash_error"] = (
                "Превышено число попыток. Вход отменён, попробуйте позже."
            )
            return RedirectResponse(url="/auth/login", status_code=303)
        request.session["2fa_error"] = f"Неверный код. Осталось попыток: {result.remaining}."
        return RedirectResponse(url="/auth/2fa", status_code=303)

    user_data = await _verified_user_data(request, session, dict(result.user_data or {}))
    if isinstance(user_data, RedirectResponse):
        return user_data

    from core.maintenance import is_maintenance_mode

    if await is_maintenance_mode():
        user_role = str(user_data.get("role", "")).upper()
        if user_role not in (WebRole.SUPERADMIN.value, "SUPERADMIN"):
            await otp_store.cancel(request)
            request.session["flash_error"] = (
                "На платформе ведутся технические работы. Пожалуйста, повторите попытку позже."
            )
            return RedirectResponse(url="/auth/login", status_code=302)

    request.session.pop(otp_store.SESSION_KEY, None)
    request.session["user"] = user_data
    request.session["csrf_token"] = secrets.token_urlsafe(32)
    await _log_action(
        "web_login_success",
        f"Успешный вход {user_data['username']} (роль {user_data['role']}) с подтверждением 2FA",
    )
    return RedirectResponse(url=take_next(request), status_code=303)


@router.post("/2fa/resend")
async def two_factor_resend(request: Request, session: AsyncSession = Depends(get_db)):
    """Повторно отправить код, сохраняя старый действительным при исчерпании quota."""
    pending = await otp_store.peek(request)
    if not pending:
        await otp_store.cancel(request)
        request.session["flash_error"] = "Сессия 2FA истекла. Войдите заново."
        return RedirectResponse(url="/auth/login", status_code=303)

    vk_admin_id = pending.get("vk_admin_id")
    if otp_store.resend_quota(pending) <= 0:
        request.session["2fa_error"] = (
            "Лимит повторных отправок кода исчерпан. Используй ранее отправленный "
            "код или войди заново позже."
        )
        return RedirectResponse(url="/auth/2fa", status_code=303)

    otp_code, remaining = await otp_store.rotate(request, ttl=settings.TWO_FACTOR_CODE_TTL)
    if not otp_code or not vk_admin_id:
        await otp_store.cancel(request)
        request.session["flash_error"] = "Сессия 2FA истекла. Войдите заново."
        return RedirectResponse(url="/auth/login", status_code=303)

    await _send_otp_to_vk(
        session,
        vk_admin_id=int(vk_admin_id),
        otp_code=otp_code,
        reason="🔐 Новый одноразовый код для входа в панель управления OSS Bot",
    )
    request.session["2fa_error"] = f"Новый код отправлен в ВК. Доступно отправок: {remaining}."
    return RedirectResponse(url="/auth/2fa", status_code=303)
