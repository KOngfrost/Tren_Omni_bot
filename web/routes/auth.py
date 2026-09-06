"""
Маршруты аутентификации веб-панели.

Безопасность:
- Пользователи хранятся в таблице web_users, пароли — только в виде
  PBKDF2-хешей (web/security/passwords.py).
- Резервный вход по WEB_ADMIN_USERNAME/WEB_ADMIN_PASSWORD из .env
  (bootstrap-суперадмин, пока web_users не заведены).
- Rate limiting: не более 5 неудачных попыток за 15 минут на IP,
  затем временная блокировка.
- Журналирование входов в таблицу logs + уведомление суперадмина в VK
  при срабатывании блокировки.
- Timing-safe сравнение, поворот CSRF-токена после входа.
"""

import json
import logging
import secrets
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select

from core import database as core_db
from core.config import settings
from core.models import Log, LoginAttempt, WebRole, WebUser
from core.vk_client import send_vk_message
from web.security.csrf import get_csrf_token
from web.security.middleware import RateLimiter
from web.security.passwords import verify_password
from web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()

CREDENTIALS_NOT_SET = (
    "Вход не настроен: создайте пользователя через scripts/create_web_user.py "
    "или задайте WEB_ADMIN_USERNAME и WEB_ADMIN_PASSWORD в .env"
)

# Rate limiting: 5 неудачных попыток за 15 минут на IP.
# Первичное хранилище — таблица login_attempts (PostgreSQL): лимит переживает
# рестарты панели и работает одинаково при нескольких экземплярах.
# _LOGIN_ATTEMPTS остаётся как in-memory mirror (совместимость; fallback,
# если БД временно недоступна).
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 15 * 60

# In-memory mirror для fallback (когда БД недоступна).
# При штатной работе используется БД (login_attempts).
_LOGIN_ATTEMPTS: dict[str, list[float]] = {}

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
# Используем RateLimiter из middleware.py вместо дублирующейся логики.
_crud_rate_limiter = RateLimiter(max_requests=20, window_seconds=5 * 60)


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


async def _db_recent_failed_count(ip: str) -> int:
    """Сколько неудачных попыток за окно в базе. -1 — БД недоступна."""
    try:
        cutoff = datetime.now(UTC) - timedelta(seconds=_LOGIN_WINDOW_SECONDS)
        async with core_db.async_session_maker() as session:
            count: int | None = await session.scalar(
                select(func.count(LoginAttempt.id)).where(
                    LoginAttempt.ip == ip,
                    LoginAttempt.success.is_(False),
                    LoginAttempt.attempted_at >= cutoff,
                )
            )
            return int(count or 0)
    except Exception:
        logger.warning("Rate-limit: БД недоступна, использую in-memory mirror")
        return -1


def _memory_window(ip: str) -> list[float]:
    """Очистить и вернуть окно неудачных попыток из in-memory mirror."""
    now = time.time()
    window_start = now - _LOGIN_WINDOW_SECONDS
    attempts = [t for t in _LOGIN_ATTEMPTS.get(ip, []) if t > window_start]
    _LOGIN_ATTEMPTS[ip] = attempts
    return attempts


async def _is_rate_limited(ip: str) -> bool:
    """Превышен ли лимит неудачных попыток входа для IP (БД + mirror)."""
    db_count = await _db_recent_failed_count(ip)
    if db_count >= 0:
        return db_count >= _LOGIN_MAX_ATTEMPTS
    return len(_memory_window(ip)) >= _LOGIN_MAX_ATTEMPTS


async def _record_failed_attempt(ip: str) -> None:
    """Зафиксировать неудачную попытку входа (в БД и в mirror)."""
    now = time.time()
    window_start = now - _LOGIN_WINDOW_SECONDS
    attempts = [t for t in _LOGIN_ATTEMPTS.get(ip, []) if t > window_start]
    attempts.append(now)
    _LOGIN_ATTEMPTS[ip] = attempts

    try:
        async with core_db.async_session_maker() as session:
            session.add(LoginAttempt(ip=ip, success=False))
            await session.commit()
    except Exception:
        logger.warning("Rate-limit: не удалось записать попытку входа в БД")


async def _clear_attempts(ip: str) -> None:
    """Сбросить лимит после успешного входа (БД + mirror)."""
    _LOGIN_ATTEMPTS.pop(ip, None)
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


async def _bootstrap_disabled_in_db() -> bool:
    """Проверить, есть ли пользователи в web_users.

    Если в БД уже есть хотя бы один пользователь, bootstrap-вход из .env
    считается недействительным — постоянные учётные данные должны
    создаваться через scripts/create_web_user.py.
    """
    try:
        async with core_db.async_session_maker() as session:
            count: int | None = await session.scalar(
                select(func.count(WebUser.id)).where(WebUser.is_active.is_(True))
            )
            return int(count or 0) == 0
    except Exception:
        # БД недоступна — безопасно разрешаем bootstrap как fallback
        logger.warning("БД недоступна: разрешаю bootstrap-вход как fallback")
        return True


async def _authenticate(username: str, password: str) -> dict | None:
    """Аутентифицировать пользователя: сначала web_users, затем .env-bootstrap.

    Bootstrap-вход из .env работает ТОЛЬКО если в web_users нет активных
    пользователей. После заведения постоянных учётных данных bootstrap
    автоматически отключается.
    """
    try:
        async with core_db.async_session_maker() as session:
            web_user = await session.scalar(
                select(WebUser).where(WebUser.username == username)
            )
            if web_user is not None:
                if not web_user.is_active:
                    return None
                if not verify_password(password, web_user.password_hash):
                    return None
                web_user.last_login_at = datetime.now()
                await session.commit()
                return {
                    "username": web_user.username,
                    "role": web_user.role.value,
                    "web_user_id": web_user.id,
                    "department_id": web_user.department_id,
                }
    except Exception:
        # БД недоступна — пробуем bootstrap-вход из .env
        logger.exception("Не удалось проверить web_users")

    # Bootstrap-вход из .env (только пока web_users пуст)
    # Пароль из .env сравнивается только если нет активных пользователей в БД.
    # Это предотвращает использование plain-text пароля из .env после
    # заведения постоянных учётных записей.
    if _credentials_configured():
        if await _bootstrap_disabled_in_db():
            username_ok = secrets.compare_digest(
                username.encode("utf-8"), settings.WEB_ADMIN_USERNAME.encode("utf-8")
            )
            password_ok = secrets.compare_digest(
                password.encode("utf-8"), settings.WEB_ADMIN_PASSWORD.encode("utf-8")
            )
            if username_ok and password_ok:
                logger.warning(
                    "Bootstrap-вход из .env выполнен (web_users пуст). "
                    "Рекомендуется создать постоянного пользователя: "
                    "python scripts/create_web_user.py"
                )
                return {
                    "username": username,
                    "role": WebRole.SUPERADMIN.value,
                    "web_user_id": None,
                    "bootstrap": True,
                    "department_id": None,
                }
        else:
            # Bootstrap отключён — но если введён правильный пароль из .env,
            # логируем предупреждение о том, что учётные данные устарели
            if username == settings.WEB_ADMIN_USERNAME:
                logger.warning(
                    "Bootstrap-вход отклонён: в web_users уже есть активные "
                    "пользователи. Используйте постоянные учётные данные или "
                    "создайте пользователя через scripts/create_web_user.py"
                )
    return None


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Страница входа."""
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": None,
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

    if await _is_rate_limited(client_ip):
        details = f"Блокировка IP {client_ip}: превышен лимит попыток входа (username={username!r})"
        await _log_action("web_login_blocked", details)
        await _notify_superadmin(details)
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Слишком много попыток входа. Подождите 15 минут.",
                "csrf_token": get_csrf_token(request),
            },
            status_code=429,
        )

    user_data: dict | None = await _authenticate(username, password)

    if user_data is None:
        await _record_failed_attempt(client_ip)
        await _log_action(
            "web_login_failed",
            f"Неудачный вход с IP {client_ip} (username={username!r})",
        )
        logger.warning("Неудачная попытка входа с IP %s", client_ip)
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Неверный логин или пароль",
                "csrf_token": get_csrf_token(request),
            },
            status_code=401,
        )

    await _clear_attempts(client_ip)
    request.session["user"] = user_data
    request.session["csrf_token"] = secrets.token_urlsafe(32)
    await _log_action(
        "web_login_success",
        f"Успешный вход {user_data['username']} (роль {user_data['role']}) с IP {client_ip}",
    )

    # Генерируем уникальный session_id для параллельных входов
    session_id = secrets.token_urlsafe(32)

    # Копируем user данные в scope["session"] для совместимости с CSRF
    if "session" in request.scope:
        request.scope["session"]["user"] = user_data
        request.scope["session"]["session_id"] = session_id

    # Сохраняем сессию в cookie с уникальным именем
    cookie_name = f"session_{session_id}"
    session_data = {"user": user_data, "session_id": session_id}
    response = RedirectResponse(url=f"/?sid={session_id}", status_code=303)
    response.set_cookie(
        cookie_name,
        json.dumps(session_data),
        max_age=3600,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return response


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

    # Определяем session_id из query-параметра
    session_id = request.query_params.get("sid")

    # Удаляем cookie сессии
    response = RedirectResponse(url="/auth/login", status_code=303)
    if session_id:
        cookie_name = f"session_{session_id}"
        response.delete_cookie(cookie_name, path="/")

    return response


# ==========================================
# Rate limiting для CRUD-операций
# ==========================================

def require_crud_rate_limit(request: Request):
    """Зависимость FastAPI для rate limiting CRUD-операций.

    Использовать как Depends(require_crud_rate_limit) на POST-маршрутах.
    Лимит: 20 операций на IP за 5 минут.
    При превышении — 429 Too Many Requests с блокировкой на 1 минуту.
    """
    client_ip = _get_client_ip(request)

    if not _crud_rate_limiter.is_allowed(client_ip):
        logger.warning("CRUD rate limit превышен для IP %s", client_ip)
        raise HTTPException(
            status_code=429,
            detail="Слишком много запросов. Подождите 1 минуту.",
        )
