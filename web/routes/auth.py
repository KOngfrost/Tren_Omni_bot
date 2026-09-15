"""
Маршруты аутентификации веб-панели.

Безопасность:
- Хеш пароля хранится в web_users.password_hash (bcrypt) и сверяется через
  web.security.passwords.verify_password.
- Bootstrap-режим: временный вход суперадминистратора из .env
  (WEB_ADMIN_USERNAME/PASSWORD), пока в базе нет ни одного постоянного
  суперадминистратора. Каждый bootstrap-вход помечается в журнале.
- Rate limiting входов ведётся ЕДИНООБРАЗНО в таблице login_attempts
  (PostgreSQL), что корректно работает при нескольких uvicorn-worker'ах.
- OTP второго фактора никогда не хранится в cookie/сессии: в сессии живёт
  только случайный токен попытки, а код и его хеш — в серверном хранилище
  web.security.otp_store.
"""

import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core import database as core_db
from core.config import settings
from core.database import get_db
from core.models import Log, LoginAttempt, WebRole, WebUser
from core.vk_client import send_vk_message
from web.security import otp_store
from web.security.csrf import get_csrf_token
from web.security.middleware import DBRateLimiter
from web.security.passwords import verify_password
from web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()

# Rate limiting: 5 неудачных попыток за 15 минут на IP.
# Единственное хранилище — таблица login_attempts (PostgreSQL).
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 15 * 60

# Rate limiting для CRUD-операций админ-панели: защита от brute-force на
# чувствительных операциях (ответ администратора, создание/передача заявок,
# создание/удаление пользователей). Лимит: 60 операций на IP за 5 минут.
_crud_rate_limiter = DBRateLimiter(
    table_name="crud_attempts",
    max_requests=60,
    window_seconds=5 * 60,
)

# 0x5C — обратный слэш. Константа вместо литерала, чтобы исходник
# читался однозначно в любом редакторе и не требовал экранирования.
_BACKSLASH = chr(0x5C)


# ==========================================
# Вспомогательные функции
# ==========================================


def _credentials_configured() -> bool:
    return bool(settings.WEB_ADMIN_USERNAME and settings.WEB_ADMIN_PASSWORD)


# ==========================================
# Возврат на запрошенную страницу после входа (?next=)
# ==========================================
# Значение next принимается ТОЛЬКО как внутренний путь ("/tickets/5").
# Внешние адреса ("//evil.com", "https://evil.com") отклоняются, иначе
# редирект после входа стал бы открытым redirect.
NEXT_SESSION_KEY = "login_next"


def safe_next_path(raw: object) -> str | None:
    """Вернуть безопасный внутренний путь или None."""
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value or not value.startswith("/") or len(value) > 2048:
        return None
    # Protocol-relative ("//host") и "/<backslash>host" записи запрещены
    if value[1:2] in {"/", _BACKSLASH}:
        return None
    if any(ch in value for ch in ("\r", "\n", "\x00")):
        return None
    return value


def remember_next(request: Request, raw: object) -> None:
    """Запомнить (или сбросить) адрес возврата после входа."""
    path = safe_next_path(raw)
    if path:
        request.session[NEXT_SESSION_KEY] = path
    else:
        request.session.pop(NEXT_SESSION_KEY, None)


def take_next(request: Request) -> str:
    """Одноразово забрать адрес возврата (по умолчанию — дашборд)."""
    return safe_next_path(request.session.pop(NEXT_SESSION_KEY, None)) or "/"


def login_url_with_next(request: Request) -> str:
    """Ссылка на вход с сохранением запрошенной страницы (для require_auth)."""
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
    """Fail-Closed: разрешён ли ещё резервный вход для этой сессии.

    Проверяется при КАЖДОМ запросе bootstrap-сессии: доверие устаревшим
    данным сессии исключено, если резервный вход отключили
    (BOOTSTRAP_ALLOWED=False), креды удалили/изменили в .env или username
    сессии больше не совпадает с активной конфигурацией.
    """
    if not settings.BOOTSTRAP_ALLOWED:
        return False
    if not _credentials_configured():
        return False
    return user.get("username") == settings.WEB_ADMIN_USERNAME


def _mask_vk_id(vk_admin_id: object) -> str:
    """Маскировать VK ID для отображения (только последние 4 цифры)."""
    digits = re.sub(r"\D", "", str(vk_admin_id or ""))
    return f"***{digits[-4:]}" if digits else "***"


def _get_client_ip(request: Request) -> str:
    """Получить реальный IP клиента.

    X-Forwarded-For принимается ТОЛЬКО от доверенных прокси
    (settings.TRUSTED_PROXIES). При прямой публикации панели подделка
    заголовка больше не позволяет обойти rate-limit.
    """
    client_ip = request.client.host if request.client else "unknown"
    if client_ip in settings.TRUSTED_PROXIES:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return client_ip


# ==========================================
# Rate limiting и журнал
# ==========================================


async def _db_recent_failed_count(session: AsyncSession, ip: str) -> int:
    """Сколько неудачных попыток входа за окно.

    При недоступности БД мягко деградирует: возвращает 0, чтобы сбой базы
    не блокировал вход всем сразу (учётки при этом проверяются fail-closed).
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=_LOGIN_WINDOW_SECONDS)
    try:
        count: int | None = await session.scalar(
            select(func.count(LoginAttempt.id)).where(
                LoginAttempt.ip == ip,
                LoginAttempt.success.is_(False),
                LoginAttempt.attempted_at >= cutoff,
            )
        )
        return int(count or 0)
    except Exception:
        logger.warning("Rate-limit: не удалось прочитать попытки входа из БД")
        return 0


async def _is_rate_limited(session: AsyncSession, ip: str) -> bool:
    """Превышен ли лимит неудачных попыток входа для IP."""
    return await _db_recent_failed_count(session, ip) >= _LOGIN_MAX_ATTEMPTS


async def _record_failed_attempt(ip: str) -> None:
    """Зафиксировать неудачную попытку входа.

    Очистка устаревших записей вынесена в фоновую задачу
    core/rate_limit_cleanup.py, чтобы не нагружать горячий путь.
    """
    try:
        async with core_db.async_session_maker() as session:
            session.add(LoginAttempt(ip=ip, success=False))
            await session.commit()
    except Exception:
        logger.warning("Rate-limit: не удалось записать попытку входа в БД")


async def _clear_attempts(ip: str) -> None:
    """Сбросить лимит после успешного входа."""
    try:
        async with core_db.async_session_maker() as session:
            await session.execute(delete(LoginAttempt).where(LoginAttempt.ip == ip))
            await session.commit()
    except Exception:
        logger.warning("Rate-limit: не удалось очистить попытки входа")


async def _log_action(action: str, details: str) -> None:
    """Записать событие входа в журнал (таблица logs)."""
    try:
        async with core_db.async_session_maker() as session:
            session.add(Log(user_id=None, action=action, details=details))
            await session.commit()
    except Exception:
        logger.exception("Не удалось записать событие входа в журнал")


async def _notify_superadmin(details: str) -> None:
    """Уведомить суперадминистратора о подозрительной активности."""
    logger.warning("Подозрительная активность: %s", details)
    if settings.VK_REPORT_ADMIN_ID:
        await send_vk_message(
            settings.VK_REPORT_ADMIN_ID,
            f"⚠️ Веб-админка: подозрительная активность\n{details}",
        )


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
            f"🔐 {reason}:\n{otp_code}\n"
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

    channel = _bootstrap_2fa_channel()
    if settings.TWO_FACTOR_ENABLED and not channel:
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
        "needs_2fa": bool(settings.TWO_FACTOR_ENABLED),
        "vk_admin_id": channel,
    }


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
                return None
            if not verify_password(password, web_user.password_hash):
                return None
            web_user.last_login_at = datetime.now(UTC)
            await session.commit()

            vk_admin_id = (
                web_user.admin.user.vk_id if web_user.admin and web_user.admin.user else None
            )
            if settings.TWO_FACTOR_ENABLED and not vk_admin_id:
                # Нет доверенного канала — вход блокируется до привязки VK
                logger.warning(
                    "SECURITY AUDIT: 2FA LOGIN BLOCKED | user=%s | "
                    "reason=привязанный VK-аккаунт не настроен",
                    web_user.username,
                )
                return None

            return {
                "username": web_user.username,
                "role": web_user.role.value if web_user.role else WebRole.DEPARTMENT_ADMIN.value,
                "web_user_id": web_user.id,
                "department_id": web_user.department_id,
                "needs_2fa": bool(settings.TWO_FACTOR_ENABLED),
                "vk_admin_id": vk_admin_id,
            }
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
    flash_error = request.session.pop("flash_error", None)
    remember_next(request, request.query_params.get("next"))
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": flash_error,
            "flash_error": flash_error,
            "csrf_token": get_csrf_token(request),
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
    form = await request.form()
    username: str = str(form.get("username", ""))
    password: str = str(form.get("password", ""))

    async with core_db.async_session_maker() as session:
        if await _is_rate_limited(session, client_ip):
            details = (
                f"Блокировка IP {client_ip}: превышен лимит попыток входа (username={username!r})"
            )
            await _log_action("web_login_blocked", details)
            await _notify_superadmin(details)
            request.session["flash_error"] = "Слишком много попыток входа. Подождите 15 минут."
            return RedirectResponse(url="/auth/login", status_code=302)

        user_data: dict | None = await _authenticate(session, username, password)

        if user_data is None:
            await _record_failed_attempt(client_ip)
            await _log_action(
                "web_login_failed",
                f"Неудачный вход с IP {client_ip} (username={username!r})",
            )
            logger.warning("Неудачная попытка входа с IP %s", client_ip)
            request.session["flash_error"] = "Неверный логин или пароль"
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
                reason="Одноразовый код для входа в панель управления OSS Bot",
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
        f"Успешный вход {user_data['username']} (роль {user_data['role']}) с IP {client_ip}",
    )
    return RedirectResponse(url=take_next(request), status_code=303)


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
    """Проверка 2FA-кода и выдача полноценной сессии.

    Fail-Closed (#2, #3): сразу после успешного ввода кода роль и активность
    учётки перечитываются из базы, а bootstrap-сессия повторно проверяется
    на то, что резервный вход всё ещё разрешён и канал 2FA не был снят.
    """
    if request.session.get("user"):
        return RedirectResponse(url=take_next(request), status_code=303)

    form = await request.form()
    # Допускается вставка пробелов/дефисов (автозаполнение менеджеров паролей)
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

    user_data = dict(result.user_data or {})

    # Fail-Closed: роль и активность перечитываются из базы, а не из токена
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
        if settings.TWO_FACTOR_ENABLED and not vk_admin_id:
            await otp_store.cancel(request)
            request.session["flash_error"] = "Привязка VK-аккаунта снята — вход с 2FA невозможен."
            return RedirectResponse(url="/auth/login", status_code=302)

        user_data = {
            "username": web_user.username,
            "role": web_user.role.value if web_user.role else WebRole.DEPARTMENT_ADMIN.value,
            "web_user_id": web_user.id,
            "department_id": web_user.department_id,
        }
    else:
        # Bootstrap-вход: резервный доступ могли отключить, пока пользователь
        # вводил код (BOOTSTRAP_ALLOWED=False, смена кредов, снятие 2FA-канала)
        if not bootstrap_session_still_valid(user_data):
            await otp_store.cancel(request)
            request.session["flash_error"] = (
                "Резервный вход отключён или перенастроен. Войдите заново."
            )
            return RedirectResponse(url="/auth/login", status_code=302)

        channel = _bootstrap_2fa_channel()
        if settings.TWO_FACTOR_ENABLED and not channel:
            await otp_store.cancel(request)
            request.session["flash_error"] = (
                "Доверенный канал 2FA больше не настроен — вход отменён."
            )
            return RedirectResponse(url="/auth/login", status_code=302)

        user_data = {
            "username": user_data.get("username") or settings.WEB_ADMIN_USERNAME,
            "role": WebRole.SUPERADMIN.value,
            "web_user_id": None,
            "department_id": None,
            "bootstrap": True,
        }

    # Успех: попытка 2FA полностью снимается, токен больше не принимается
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
    """Повторная отправка 2FA-кода (старый код немедленно аннулируется)."""
    pending = await otp_store.peek(request)
    if not pending:
        await otp_store.cancel(request)
        request.session["flash_error"] = "Сессия 2FA истекла. Войдите заново."
        return RedirectResponse(url="/auth/login", status_code=303)

    vk_admin_id = pending.get("vk_admin_id")

    # Лимит повторных отправок: предыдущий код остаётся действительным,
    # новый не генерируется и в VK не отправляется (защита от флуда канала)
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
        reason="Новый одноразовый код для входа в панель управления OSS Bot",
    )

    request.session["2fa_error"] = f"Новый код отправлен в ВК. Доступно отправок: {remaining}."
    return RedirectResponse(url="/auth/2fa", status_code=303)


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
            logger.warning("CRUD rate limit превышен для IP %s", client_ip)
            raise HTTPException(
                status_code=429,
                detail="Слишком много запросов. Подождите минуту.",
            )


__all__ = [
    "NEXT_SESSION_KEY",
    "bootstrap_session_still_valid",
    "login_url_with_next",
    "remember_next",
    "require_crud_rate_limit",
    "router",
    "safe_next_path",
    "take_next",
]
