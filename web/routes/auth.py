"""
Маршруты аутентификации веб-панели.

Безопасность:
- Пользователи хранятся в таблице web_users, пароли — только в виде
  PBKDF2-хешей (web/security/passwords.py).
- Резервный вход по WEB_ADMIN_USERNAME/WEB_ADMIN_PASSWORD из .env
  (bootstrap-суперадмин, пока web_users не заведены).
- Rate limiting: не более 5 неудачных попыток за 15 минут на IP,
  затем временная блокировка (хранилище — БД login_attempts).
- Журналирование входов в таблицу logs + уведомление суперадмина в VK
  при срабатывании блокировки.
- Timing-safe сравнение, поворот CSRF-токена после входа.
"""

import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core import database as core_db
from core.config import settings
from core.models import Log, LoginAttempt, WebRole, WebUser
from core.vk_client import send_vk_message
from web.security.csrf import get_csrf_token
from web.security.middleware import DBRateLimiter
from web.security.passwords import verify_password
from web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()

CREDENTIALS_NOT_SET = (
    "Вход не настроен: создайте пользователя через scripts/create_web_user.py "
    "или задайте WEB_ADMIN_USERNAME и WEB_ADMIN_PASSWORD в .env"
)

# Rate limiting: 5 неудачных попыток за 15 минут на IP.
# Единственное хранилище — таблица login_attempts (PostgreSQL).
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 15 * 60

# ==========================================
# Rate limiting для CRUD-операций админ-панели
# ==========================================
# Защита от brute-force на чувствительных операциях:
# - Смена статуса заявки
# - Ответ администратора
# - Создание/передача заявок
# - Создание/удаление пользователей админа
#
# Лимит: 20 операций на IP за 5 минут → временная блокировка (1 минута).
# Используем DBRateLimiter для многопроцессной совместимости.
_crud_rate_limiter = DBRateLimiter(
    table_name="crud_attempts",
    max_requests=20,
    window_seconds=5 * 60,
)


def _credentials_configured() -> bool:
    return bool(settings.WEB_ADMIN_USERNAME and settings.WEB_ADMIN_PASSWORD)


def _get_client_ip(request: Request) -> str:
    """Получить реальный IP клиента.

    Заголовок X-Forwarded-For принимается ТОЛЬКО от доверенных прокси
    (settings.TRUSTED_PROXIES). При прямой публикации панели (без reverse proxy)
    подделка заголовка больше не позволяет обойти rate-limit.
    """
    client_ip = request.client.host if request.client else "unknown"
    if client_ip in settings.TRUSTED_PROXIES:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return client_ip


async def _db_recent_failed_count(session: AsyncSession, ip: str) -> int:
    """Сколько неудачных попыток за окно в базе.

    При недоступности БД мягко деградирует: возвращает 0, чтобы
    не блокировать вход при проблемах с базой (лучше пропустить,
    чем выдать 500 и заблокировать всех).
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
    db_count = await _db_recent_failed_count(session, ip)
    return db_count >= _LOGIN_MAX_ATTEMPTS


async def _record_failed_attempt(ip: str) -> None:
    """Зафиксировать неудачную попытку входа в БД."""
    try:
        async with core_db.async_session_maker() as session:
            session.add(LoginAttempt(ip=ip, success=False))
            await session.commit()
    except Exception:
        logger.warning("Rate-limit: не удалось записать попытку входа в БД")


async def _clear_attempts(ip: str) -> None:
    """Сбросить лимит после успешного входа (БД)."""
    try:
        async with core_db.async_session_maker() as session:
            await session.execute(delete(LoginAttempt).where(LoginAttempt.ip == ip))
            await session.commit()
    except Exception:
        logger.warning("Rate-limit: не удалось очистить попытки в БД")


async def _log_action(action: str, details: str) -> None:
    """Записать событие входа в журнал (таблица logs)."""
    try:
        async with core_db.async_session_maker() as session:
            session.add(Log(user_id=None, action=action, details=details))
            await session.commit()
    except Exception:
        logger.exception("Не удалось записать событие входа в журнал")


async def _notify_superadmin(details: str) -> None:
    """Уведомить суперадминистратора о подозрительной активности (VK + журнал)."""
    logger.warning("Подозрительная активность: %s", details)
    if settings.VK_REPORT_ADMIN_ID:
        await send_vk_message(
            settings.VK_REPORT_ADMIN_ID,
            f"⚠️ Веб-админка: подозрительная активность\n{details}",
        )


async def _authenticate(
    session: AsyncSession,
    username: str,
    password: str,
) -> dict | None:
    """Аутентифицировать пользователя: сначала web_users, затем .env-bootstrap.

    Использует переданную сессию вместо создания нового подключения.
    Bootstrap-вход из .env работает для суперадмина всегда.
    После заведения постоянных учётных записей bootstrap для обычных
    пользователей отключается.
    """
    try:
        web_user = await session.scalar(select(WebUser).where(WebUser.username == username))
        if web_user is not None:
            if not web_user.is_active:
                return None
            if not verify_password(password, web_user.password_hash):
                return None
            web_user.last_login_at = datetime.now(UTC)
            await session.commit()
            return {
                "username": web_user.username,
                "role": web_user.role.value if web_user.role else WebRole.DEPARTMENT_ADMIN.value,
                "web_user_id": web_user.id,
                "department_id": web_user.department_id,
            }
    except Exception:
        # БД недоступна — пробуем bootstrap-вход из .env
        logger.exception("Не удалось проверить web_users")

    # Bootstrap-вход из .env
    if _credentials_configured() and username == settings.WEB_ADMIN_USERNAME:
        password_ok = secrets.compare_digest(
            password.encode("utf-8"), settings.WEB_ADMIN_PASSWORD.encode("utf-8")
        )
        if password_ok:
            # Проверяем, есть ли в БД постоянный пользователь с таким логином
            try:
                web_user = await session.scalar(
                    select(WebUser).where(WebUser.username == username)
                )
                if web_user is None:
                    # Постоянного пользователя с таким логином нет —
                    # разрешаем bootstrap-вход (суперадмин)
                    logger.warning(
                        "Bootstrap-вход из .env выполнен (пользователь %s не найден в БД). "
                        "Рекомендуется создать постоянного пользователя: "
                        "python scripts/create_web_user.py",
                        username,
                    )
                    return {
                        "username": username,
                        "role": WebRole.SUPERADMIN.value,
                        "web_user_id": None,
                        "bootstrap": True,
                        "department_id": None,
                    }
                else:
                    # Постоянный пользователь с таким логином существует —
                    # bootstrap отклонён, нужно использовать хешированный пароль
                    logger.warning(
                        "Bootstrap-вход отклонён для пользователя %s: "
                        "постоянный пользователь уже существует в БД. "
                        "Используйте его хешированный пароль или создайте нового пользователя через scripts/create_web_user.py",
                        username,
                    )
            except Exception:
                # БД недоступна — безопасно разрешаем bootstrap как fallback
                logger.warning("БД недоступна: разрешаю bootstrap-вход как fallback")
                return {
                    "username": username,
                    "role": WebRole.SUPERADMIN.value,
                    "web_user_id": None,
                    "bootstrap": True,
                    "department_id": None,
                }
    return None


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Страница входа."""
    flash_error = request.session.pop("flash_error", None)
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": flash_error,
            "flash_error": flash_error,
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post("/login")
async def login(request: Request):
    """Обработка входа с rate limiting и журналированием."""
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

        await _clear_attempts(client_ip)
        request.session["user"] = user_data
        request.session["csrf_token"] = secrets.token_urlsafe(32)
        await _log_action(
            "web_login_success",
            f"Успешный вход {user_data['username']} (роль {user_data['role']}) с IP {client_ip}",
        )

        return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    """Выход из системы (POST с CSRF-токеном, а не GET).

    Полная инвалидация сессии:
    - Очистка всех данных сессии
    - Генерация нового CSRF-токена (старый становится невалидным)
    - Удаление session cookie (чтобы избежать повторного использования)
    """
    user_data: dict | None = request.session.get("user")
    username: str = user_data.get("username", "unknown") if user_data else "unknown"

    # Логируем выход
    await _log_action(
        "web_logout",
        f"Выполнен выход пользователя {username}",
    )

    # Полная инвалидация сессии
    request.session.clear()

    # Генерируем новый CSRF-токен — старый становится невалидным.
    # Это предотвращает повторную активацию сессии при stateless-сессиях.
    request.session["csrf_token"] = secrets.token_urlsafe(32)

    response = RedirectResponse(url="/auth/login", status_code=303)
    return response


# ==========================================
# Rate limiting для CRUD-операций
# ==========================================


async def require_crud_rate_limit(request: Request):
    """Зависимость FastAPI для rate limiting CRUD-операций.

    Использовать как Depends(require_crud_rate_limit) на POST-маршрутах.
    Лимит: 20 операций на IP за 5 минут.
    При превышении — 429 Too Many Requests с блокировкой на 1 минуту.
    """
    client_ip = _get_client_ip(request)

    async with core_db.async_session_maker() as session:
        if not await _crud_rate_limiter.is_allowed(session, client_ip, "crud_operation"):
            logger.warning("CRUD rate limit превышен для IP %s", client_ip)
            raise HTTPException(
                status_code=429,
                detail="Слишком много запросов. Подождите 1 минуту.",
            )
