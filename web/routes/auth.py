"""Маршруты аутентификации веб-панели.

Login throttling находится в ``web.security.login_rate_limiter``; функции
оставлены здесь как aliases для совместимости существующих callers/tests.
"""

import ipaddress
import logging
import re
import secrets
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core import database as core_db
from core.config import settings
from core.models import Log, WebRole, WebUser
from core.two_factor import is_two_factor_enabled
from core.vk_client import send_vk_message
from web.security import otp_store
from web.security.csrf import get_csrf_token
from web.security.login_rate_limiter import (
    LOGIN_MAX_ATTEMPTS as _LOGIN_MAX_ATTEMPTS,
)
from web.security.login_rate_limiter import (
    LOGIN_WINDOW_SECONDS as _LOGIN_WINDOW_SECONDS,
)
from web.security.login_rate_limiter import (
    clear_attempts as _clear_attempts,
)
from web.security.login_rate_limiter import (
    is_rate_limited as _is_rate_limited,
)
from web.security.login_rate_limiter import (
    record_failed_attempt as _record_failed_attempt,
)
from web.security.middleware import DBRateLimiter, mask_ip_for_logs
from web.security.passwords import verify_dummy_password, verify_password
from web.templating import templates

logger = logging.getLogger(__name__)
router = APIRouter()
_crud_rate_limiter = DBRateLimiter(
    table_name="crud_attempts",
    max_requests=settings.CRUD_RATE_LIMIT_MAX,
    window_seconds=settings.CRUD_RATE_LIMIT_WINDOW,
)
_BACKSLASH = chr(0x5C)


def _credentials_configured() -> bool:
    return bool(settings.WEB_ADMIN_USERNAME and settings.WEB_ADMIN_PASSWORD)


NEXT_SESSION_KEY = "login_next"


def safe_next_path(raw: object) -> str | None:
    """Return a safe internal redirect path, or None."""
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value or not value.startswith("/") or len(value) > 2048:
        return None
    if value[1:2] in {"/", _BACKSLASH}:
        return None
    if any(character in value for character in ("\r", "\n", "\x00")):
        return None
    return value


def remember_next(request: Request, raw: object) -> None:
    if raw is None:
        return
    path = safe_next_path(raw)
    if path:
        request.session[NEXT_SESSION_KEY] = path
    elif isinstance(raw, str) and not raw.strip():
        request.session.pop(NEXT_SESSION_KEY, None)


def take_next(request: Request) -> str:
    return safe_next_path(request.session.pop(NEXT_SESSION_KEY, None)) or "/"


def login_url_with_next(request: Request) -> str:
    if request.method.upper() != "GET":
        return "/auth/login"
    path = request.url.path
    if request.url.query:
        path = f"{path}?{request.url.query}"
    target = safe_next_path(path)
    if not target:
        return "/auth/login"
    return f"/auth/login?next={quote(target, safe='/?&=')}"


def bootstrap_session_still_valid(user: dict) -> bool:
    if user.get("telegram_id"):
        return bool(
            settings.TELEGRAM_ADMIN_ID > 0
            and user.get("telegram_id") == settings.TELEGRAM_ADMIN_ID
        )
    return bool(
        settings.BOOTSTRAP_ALLOWED
        and _credentials_configured()
        and user.get("username") == settings.WEB_ADMIN_USERNAME
    )


def _mask_vk_id(vk_admin_id: object) -> str:
    digits = re.sub(r"\D", "", str(vk_admin_id or ""))
    return f"***{digits[-4:]}" if digits else "***"


def is_trusted_proxy(ip_str: str) -> bool:
    if not ip_str or ip_str == "unknown":
        return False
    if ip_str in settings.TRUSTED_PROXIES:
        return True
    try:
        address = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    for trusted in settings.TRUSTED_PROXIES:
        if not trusted:
            continue
        try:
            if "/" in trusted:
                if address in ipaddress.ip_network(trusted, strict=False):
                    return True
            elif address == ipaddress.ip_address(trusted):
                return True
        except ValueError:
            continue
    return False


def _get_client_ip(request: Request) -> str:
    client_ip = request.client.host if request.client else "unknown"
    if is_trusted_proxy(client_ip):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            candidate = forwarded.split(",")[0].strip()
            if candidate:
                return candidate
    return client_ip


# ==========================================
# Rate limiting и журнал
# ==========================================


async def _log_action(action: str, details: str) -> None:
    """Записать событие входа в журнал (таблица logs)."""
    try:
        async with core_db.async_session_maker() as session:
            session.add(Log(user_id=None, action=action, details=details))
            await session.commit()
    except Exception:
        logger.exception("Не удалось записать событие входа в журнал")


async def _notify_superadmin(details: str) -> None:
    """Уведомить суперадминистратора о подозрительной активности (с fallback на Telegram)."""
    logger.warning("Подозрительная активность: %s", details)
    notified = False
    if settings.VK_REPORT_ADMIN_ID:
        try:
            await send_vk_message(
                settings.VK_REPORT_ADMIN_ID,
                f"Веб-админка: подозрительная активность\n{details}",
            )
            notified = True
        except Exception:
            logger.exception("Не удалось отправить VK-уведомление администратору")

    if not notified and settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_ADMIN_ID > 0:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": settings.TELEGRAM_ADMIN_ID,
                        "text": f"Веб-админка: подозрительная активность\n{details}",
                    },
                )
        except Exception:
            logger.exception("Не удалось отправить Telegram-уведомление администратору")


# ==========================================
# Аутентификация
# ==========================================


def _bootstrap_2fa_channel() -> int | None:
    """Доверенный канал доставки OTP для bootstrap-суперадмина из .env.

    У постоянного веб-пользователя канал берётся из привязки
    ``Admin.user.vk_id``; у bootstrap-входа записи в БД нет, поэтому
    используется явная настройка WEB_ADMIN_2FA_VK_ID (либо VK_REPORT_ADMIN_ID).
    """
    if settings.WEB_ADMIN_2FA_VK_ID:
        return int(settings.WEB_ADMIN_2FA_VK_ID)
    if settings.VK_REPORT_ADMIN_ID:
        return int(settings.VK_REPORT_ADMIN_ID)
    return None


async def _send_otp_to_vk(
    session: AsyncSession,
    *,
    vk_admin_id: int,
    otp_code: str,
    reason: str,
) -> None:
    """Отправить OTP через Transactional Outbox и попытаться доставить сразу.

    Фоновый outbox-воркер гарантирует повторную доставку, если прямая
    отправка не удалась (например, VK API временно недоступен).
    """
    from core.outbox import add_outbox_message, fire_outbox_delivery

    add_outbox_message(
        session,
        vk_id=vk_admin_id,
        text=(
            f"{reason}:\n{otp_code}\n"
            f"Код действителен {settings.TWO_FACTOR_CODE_TTL // 60} мин. "
            "Если это были не вы, немедленно смените пароль."
        ),
    )
    await session.commit()
    fire_outbox_delivery()


async def _authenticate_bootstrap(
    session: AsyncSession,
    username: str,
    password: str,
) -> dict | None:
    """Резервный вход суперадминистратора из .env.

    Fail-Closed:
    - при недоступной БД вход отклоняется — нельзя ни проверить отсутствие
      постоянных суперадминистраторов, ни доставить OTP;
    - при TWO_FACTOR_ENABLED вход без доверенного канала 2FA отклоняется.
    """
    if not _credentials_configured() or username != settings.WEB_ADMIN_USERNAME:
        return None

    if not settings.BOOTSTRAP_ALLOWED:
        logger.warning(
            "SECURITY AUDIT: BOOTSTRAP_LOGIN REJECTED (BOOTSTRAP_ALLOWED=False) | user=%s",
            username,
        )
        return None

    password_ok = secrets.compare_digest(
        password.encode("utf-8"), settings.WEB_ADMIN_PASSWORD.encode("utf-8")
    )
    if not password_ok:
        return None

    two_factor_on = await is_two_factor_enabled()
    channel = _bootstrap_2fa_channel()
    if two_factor_on and not channel:
        logger.warning(
            "SECURITY AUDIT: BOOTSTRAP_LOGIN BLOCKED | user=%s | "
            "reason=2FA включена, но доверенный канал не настроен "
            "(WEB_ADMIN_2FA_VK_ID / VK_REPORT_ADMIN_ID)",
            username,
        )
        return None

    try:
        active_superadmins = (
            await session.scalar(
                select(func.count(WebUser.id)).where(
                    WebUser.role == WebRole.SUPERADMIN,
                    WebUser.is_active.is_(True),
                )
            )
            or 0
        )
        if active_superadmins > 0 and not settings.FORCE_BOOTSTRAP_OVERRIDE:
            logger.warning(
                "SECURITY AUDIT: BOOTSTRAP_LOGIN BLOCKED | user=%s | "
                "reason=active_superadmins_exist (%s found)",
                username,
                active_superadmins,
            )
            return None

        web_user = await session.scalar(select(WebUser).where(WebUser.username == username))
        if web_user is not None:
            logger.warning(
                "SECURITY AUDIT: BOOTSTRAP_LOGIN REJECTED for user %s: permanent web_user exists",
                username,
            )
            return None
    except Exception:
        logger.exception(
            "SECURITY AUDIT: BOOTSTRAP_LOGIN REJECTED | user=%s | reason=database unavailable "
            "(fail-closed)",
            username,
        )
        return None

    logger.warning(
        "SECURITY AUDIT: BOOTSTRAP_LOGIN SUCCESS | user=%s | (no matching web_user in DB)",
        username,
    )
    return {
        "username": username,
        "role": WebRole.SUPERADMIN.value,
        "web_user_id": None,
        "bootstrap": True,
        "department_id": None,
        "needs_2fa": bool(two_factor_on),
        "vk_admin_id": channel,
    }


def _is_temporary_web_user(web_user: WebUser) -> bool:
    """Временная / QA-учётная запись, для которой 2FA неприменима.

    Такие записи создаются из панели без привязки к VK-администратору
    (см. ``web/routes/admin_panel.py``): код подтверждения доставлять некуда.
    Отличаем их по ограниченному сроку жизни либо по служебному префиксу
    логина (``qa_`` / ``test_`` / ``temp_``).
    """
    if web_user.expires_at is not None:
        return True
    return web_user.username.lower().startswith(("qa_", "test_", "temp_"))


async def _authenticate(
    session: AsyncSession,
    username: str,
    password: str,
) -> dict | None:
    """Аутентифицировать пользователя: сначала web_users, затем .env-bootstrap.

    Все роли веб-панели (SUPERADMIN, DEPARTMENT_ADMIN) привилегированные,
    поэтому при TWO_FACTOR_ENABLED второй фактор обязателен для любого входа.
    """
    try:
        from core.models import Admin

        web_user = await session.scalar(
            select(WebUser)
            .options(selectinload(WebUser.admin).selectinload(Admin.user))
            .where(WebUser.username == username)
        )
        if web_user is not None:
            if not web_user.is_active:
                verify_dummy_password(password)
                return None
            # Проверяем срок через свойство модели: оно само приводит наивные
            # даты (SQLite) к UTC. Прямое сравнение с datetime.now(UTC)
            # падало с TypeError на offset-naive значениях.
            if web_user.is_expired:
                logger.warning(
                    "LOGIN BLOCKED: account expired for %s (expired at %s)",
                    web_user.username,
                    web_user.expires_at,
                )
                verify_dummy_password(password)
                return None
            if not verify_password(password, web_user.password_hash):
                return None
            web_user.last_login_at = datetime.now(UTC)
            await session.commit()

            vk_admin_id = (
                web_user.admin.user.vk_id if web_user.admin and web_user.admin.user else None
            )
            two_factor_global = await is_two_factor_enabled()

            # Правила применения 2FA:
            #  - глобальный выключатель — мастер-режим для всей панели;
            #  - ЛИЧНЫЙ переключатель web_user.two_factor_enabled — решение
            #    каждого администратора о своём аккаунте;
            #  - код можно доставить только по привязке к VK, поэтому без неё:
            #      * временная / QA-учётная запись входит по паролю — она
            #        ограничена сроком и не является постоянным админом;
            #      * постоянная учётная запись вход получает ЗАКРЫТЫМ (fail
            #        closed): подтвердить вход нечем, значит войти нельзя.
            needs_2fa = bool(two_factor_global) and bool(web_user.two_factor_enabled)
            if not vk_admin_id and needs_2fa:
                if not _is_temporary_web_user(web_user):
                    logger.warning(
                        "SECURITY AUDIT: 2FA LOGIN BLOCKED | user=%s | "
                        "reason=привязанный VK-аккаунт не настроен",
                        web_user.username,
                    )
                    return None
                logger.warning(
                    "SECURITY AUDIT: 2FA SKIPPED (no VK channel) | user=%s | "
                    "reason=временная учётная запись без привязки VK",
                    web_user.username,
                )
                needs_2fa = False

            return {
                "username": web_user.username,
                "role": web_user.role.value if web_user.role else WebRole.DEPARTMENT_ADMIN.value,
                "web_user_id": web_user.id,
                "department_id": web_user.department_id,
                "needs_2fa": needs_2fa,
                "vk_admin_id": vk_admin_id,
            }
        else:
            # Защита от timing-атак: выравнивание времени при отсутствии пользователя
            verify_dummy_password(password)
    except Exception:
        # БД недоступна — вход постоянного пользователя невозможен,
        # далее проверяется только bootstrap-путь (он тоже fail-closed)
        logger.exception("Не удалось проверить web_users")

    return await _authenticate_bootstrap(session, username, password)


# ==========================================
# Маршруты
# ==========================================


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Страница входа.

    Параметр `next` (внутренний путь) запоминается в сессии, чтобы после
    успешного входа вернуть пользователя на исходную страницу.
    """
    from core.maintenance import is_maintenance_mode

    maintenance_active = (
        getattr(request.state, "maintenance_active", False) or await is_maintenance_mode()
    )
    flash_error = request.session.pop("flash_error", None)
    remember_next(request, request.query_params.get("next"))
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": flash_error,
            "flash_error": flash_error,
            "csrf_token": get_csrf_token(request),
            "maintenance_active": maintenance_active,
        },
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@router.post("/login")
async def login(request: Request):
    """Обработка входа с rate limiting, журналированием и вторым фактором."""
    client_ip = _get_client_ip(request)
    masked_client_ip = mask_ip_for_logs(client_ip)
    form = await request.form()
    username: str = str(form.get("username", ""))
    password: str = str(form.get("password", ""))

    async with core_db.async_session_maker() as session:
        if await _is_rate_limited(session, client_ip):
            details = f"Блокировка IP {masked_client_ip}: превышен лимит попыток входа (username={username!r})"
            await _log_action("web_login_blocked", details)
            await _notify_superadmin(details)
            request.session["flash_error"] = "Слишком много попыток входа. Подождите 15 минут."
            return RedirectResponse(url="/auth/login", status_code=302)

        user_data: dict | None = await _authenticate(session, username, password)

        if user_data is None:
            await _record_failed_attempt(client_ip)
            masked_user = (
                f"{username[0]}***{username[-1]}"
                if len(username) > 2
                else ("***" if username else "<empty>")
            )
            await _log_action(
                "web_login_failed",
                f"Неудачный вход с IP {masked_client_ip} (username={masked_user!r})",
            )
            logger.warning("Неудачная попытка входа с IP %s", masked_client_ip)
            request.session["flash_error"] = "Неверный логин или пароль"
            return RedirectResponse(url="/auth/login", status_code=302)

        # Во время техработ разрешён вход только суперадминистраторам
        from core.maintenance import is_maintenance_mode

        if await is_maintenance_mode():
            user_role = str(user_data.get("role", "")).upper()
            if user_role not in (WebRole.SUPERADMIN.value, "SUPERADMIN"):
                logger.warning(
                    "SECURITY AUDIT: LOGIN REJECTED (MAINTENANCE_ACTIVE) | user=%s role=%s",
                    user_data.get("username"),
                    user_role,
                )
                request.session["flash_error"] = (
                    "На платформе ведутся технические работы. Пожалуйста, повторите попытку позже."
                )
                return RedirectResponse(url="/auth/login", status_code=302)

        # Второй фактор обязателен для всех привилегированных учёток
        if user_data.get("needs_2fa"):
            vk_admin_id = user_data.get("vk_admin_id")
            if not vk_admin_id:
                # Defensive: канал обязан быть определён в _authenticate
                await otp_store.cancel(request)
                request.session["flash_error"] = (
                    "Включена двухфакторная аутентификация, но доверенный канал "
                    "не настроен. Обратитесь к суперадминистратору."
                )
                return RedirectResponse(url="/auth/login", status_code=302)

            # Открытый код живёт только здесь: в хранилище уходит его хеш
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
                reason="🔐 Одноразовый код для входа в панель управления OSS Bot",
            )
            logger.info(
                "2FA OTP код отправлен в VK для админа vk_id=%s (username=%s)",
                vk_admin_id,
                user_data["username"],
            )
            return RedirectResponse(url="/auth/2fa", status_code=303)

        await _clear_attempts(client_ip)
        request.session["user"] = user_data
        # Новый CSRF-токен после привилегированной операции (fixation protection)
        request.session["csrf_token"] = secrets.token_urlsafe(32)

    await _log_action(
        "web_login_success",
        f"Успешный вход {user_data['username']} (роль {user_data['role']}) с IP {masked_client_ip}",
    )
    return RedirectResponse(url=take_next(request), status_code=303)


@router.post("/logout")
async def logout(request: Request):
    """Выход из системы (POST с CSRF-токеном).

    Полная инвалидация сессии: очистка данных, отзыв 2FA-попытки, новый
    CSRF-токен (старый становится невалидным), удаление session cookie.
    """
    user_data: dict | None = request.session.get("user")
    username: str = user_data.get("username", "unknown") if user_data else "unknown"

    await otp_store.cancel(request)
    await _log_action("web_logout", f"Выполнен выход пользователя {username}")

    request.session.clear()
    request.session["csrf_token"] = secrets.token_urlsafe(32)

    return RedirectResponse(url="/auth/login", status_code=303)


async def require_crud_rate_limit(request: Request):
    """Зависимость FastAPI для rate limiting CRUD-операций.

    Использовать как Depends(require_crud_rate_limit) на POST-маршрутах.
    Лимит: 60 операций на IP за 5 минут, при превышении — 429.
    """
    client_ip = _get_client_ip(request)

    async with core_db.async_session_maker() as session:
        if not await _crud_rate_limiter.is_allowed(session, client_ip, "crud_operation"):
            logger.warning("CRUD rate limit превышен для IP %s", mask_ip_for_logs(client_ip))
            raise HTTPException(
                status_code=429,
                detail="Слишком много запросов. Подождите минуту.",
            )


def _register_two_factor_router() -> None:
    from web.routes.two_factor import router as two_factor_router

    router.include_router(two_factor_router)


_register_two_factor_router()


__all__ = [
    "NEXT_SESSION_KEY",
    "bootstrap_session_still_valid",
    "login_url_with_next",
    "remember_next",
    "require_crud_rate_limit",
    "router",
    "safe_next_path",
    "take_next",
    "_clear_attempts",
    "_is_rate_limited",
    "_record_failed_attempt",
    "_LOGIN_MAX_ATTEMPTS",
    "_LOGIN_WINDOW_SECONDS",
]
