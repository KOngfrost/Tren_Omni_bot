"""
Главный модуль FastAPI-приложения.

Включает:
- Поддержка параллельных сессий через уникальные session_id (SessionAuthMiddleware)
- CSRF-защита (CSRFMiddleware)
- Security Headers (X-Frame-Options, CSP, X-Content-Type-Options и др.)
- Валидация размера запросов
- Безопасный глобальный обработчик ошибок
- Кэширование department_name для middleware
- Логирование в файлы (core/logging_config.py)
"""

import logging
import os
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from core.config import settings
from core.database import async_session_maker

# Настраиваем логирование при старте веб-панели
from core.logging_config import setup_logging
from web.security.csrf import CSRFMiddleware
from web.security.middleware import (
    RequestSizeValidator,
    SecurityHeadersMiddleware,
)
from web.security.session_middleware_asgi import SessionAuthMiddleware
from web.templating import templates  # noqa: F401 (реэкспорт для обратной совместимости)

setup_logging(log_dir=os.path.join(os.path.dirname(__file__), "..", "logs"))

logger = logging.getLogger(__name__)

app = FastAPI(
    title="oss-web-panel",
    description="Веб-админка OSS Bot: управление обращениями студентов",
    version="2.0.0",
)

# === Middleware безопасности ===
# ВАЖНО: В Starlette add_middleware использует insert(0, ...), поэтому
# порядок выполнения ОБРАТЕН порядку добавления.
# Первым добавленный выполняется ПОСЛЕДНИМ (innermost).
# Целевой порядок на запросе: SecurityHeaders → Session → SessionAuth → CSRF → CORS → handler
# Поэтому добавляем в ОБРАТНОМ порядке: CORS → CSRF → SessionAuth → Session → SecurityHeaders

# 1. CORS (innermost - выполняется последним перед handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. CSRF защита для всех POST-запросов
# ДОЛЖЕН выполняться ПОСЛЕ SessionMiddleware, чтобы scope["session"] существовал
app.add_middleware(CSRFMiddleware)

# 3. Поддержка параллельных сессий через уникальные session_id
# Каждый пользователь получает уникальный session_id, который используется
# для имени cookie: session_<session_id>. session_id передаётся через
# query-параметр ?sid=<session_id>.
# ДОЛЖЕН выполняться ПОСЛЕ SessionMiddleware, чтобы scope["session"] существовал.
app.add_middleware(SessionAuthMiddleware)

# 4. Session middleware с безопасными настройками (для CSRF)
# ДОЛЖЕН выполняться ДО SessionAuthMiddleware и CSRFMiddleware,
# чтобы scope["session"] был создан до того, как они попытаются его прочитать.
_session_secret = settings.session_secret_key
if not _session_secret:
    raise RuntimeError(
        "SESSION_SECRET_KEY не задан. Установите его в .env и перезапустите панель. "
        "Генерация: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
    )

# Жёсткие проверки для production (дефолтные креды БД, слабый секрет и т.п.)
settings.ensure_production_config()

app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    max_age=3600,
    https_only=settings.SESSION_HTTPS_ONLY,
    same_site="strict",
    path="/",
)

# 5. Security Headers — X-Frame-Options, CSP, X-Content-Type-Options и др.
# (outermost - выполняется первым)
app.add_middleware(SecurityHeadersMiddleware)

# Static
app.mount("/static", StaticFiles(directory="web/static"), name="static")

# Валидатор размера запроса (10MB лимит)
_request_size_validator = RequestSizeValidator(max_body_size=10 * 1024 * 1024)


# Middleware для валидации размера запросов
@app.middleware("http")
async def validate_request_size(request: Request, call_next):
    """Проверяет размер запроса до его обработки."""
    await _request_size_validator.check_form_size(request)
    response = await call_next(request)
    return response


# === Кэширование department_name ===

# Простой in-memory кэш: {web_user_id: (department_name, user_departments, dept_id, expires_at)}
_department_cache: dict[int, tuple[str | None, list, int | None, float]] = {}
_DEPARTMENT_CACHE_TTL = 300  # 5 минут


def _get_cached_department(user_id: int) -> tuple[str | None, list, int | None] | None:
    """Получить department_name из кэша. Возвращает None, если кэш истёк."""
    entry = _department_cache.get(user_id)
    if entry is None:
        return None
    cached_name, cached_depts, cached_dept_id, cached_at = entry
    if time.time() - cached_at > _DEPARTMENT_CACHE_TTL:
        del _department_cache[user_id]
        return None
    return cached_name, cached_depts, cached_dept_id


def _set_department_cache(user_id: int, name: str | None, depts: list, dept_id: int | None) -> None:
    """Сохранить department_name в кэш."""
    _department_cache[user_id] = (name, depts, dept_id, time.time())


# Middleware для загрузки department_name в контекст шаблонов
@app.middleware("http")
async def add_department_name(request: Request, call_next):
    """Загружает department_name и список отделов пользователя из БД (с кэшем)."""
    # Пропускаем статические файлы, healthcheck, auth и API-запросы
    if (
        request.url.path.startswith("/static/")
        or request.url.path == "/health"
        or request.url.path.startswith("/auth/")
        or "application/json" in request.headers.get("accept", "")
    ):
        return await call_next(request)

    try:
        user = request.session.get("user")
        if user:
            from web.dependencies import get_admin_scope, get_departments_for_user

            web_user_id = user.get("web_user_id")
            # Попытка получить из кэша
            cached = _get_cached_department(web_user_id) if web_user_id else None
            if cached is not None:
                department_name, user_departments, dept_id = cached
            else:
                async with async_session_maker() as session:
                    user_departments = await get_departments_for_user(session, user)
                    is_super, _dept_id = await get_admin_scope(session, user)

                department_name = (
                    user_departments[0].name if not is_super and user_departments else None
                )
                dept_id = None
                if request.url.path.startswith("/dept/"):
                    parts = request.url.path.strip("/").split("/")
                    if len(parts) >= 2 and parts[1].isdigit():
                        dept_id = int(parts[1])

                # Сохраняем в кэш
                if web_user_id:
                    _set_department_cache(web_user_id, department_name, user_departments, dept_id)

            request.state.department_name = department_name
            request.state.user_departments = user_departments
            request.state.dept_id = dept_id
    except Exception:
        # Если не удалось загрузить — продолжаем без department_name
        pass

    response = await call_next(request)
    return response


# === Глобальный обработчик ошибок — без раскрытия деталей ===

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Сохранить корректный HTTP-статус для отказов auth и CSRF."""
    if exc.status_code in (302, 303) and exc.headers:
        return RedirectResponse(
            url=exc.headers.get("Location", "/auth/login"),
            status_code=exc.status_code,
            headers=exc.headers,
        )
    return JSONResponse(
        content={"detail": exc.detail},
        status_code=exc.status_code,
        headers=exc.headers,
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Обрабатывает все необработанные исключения БЕЗ раскрытия деталей.

    В dev-режиме возвращает traceback для удобства отладки.
    В production — обобщённое сообщение + подробный лог.
    """
    request_id = uuid.uuid4().hex[:12]
    logger.exception(
        "Необработанное исключение на %s [request_id=%s]", request.url.path, request_id
    )

    is_api = (
        request.url.path.startswith("/tickets/")
        and "application/json" in request.headers.get("accept", "")
    )

    if not settings.IS_PRODUCTION:
        # В dev-режиме — подробности для отладки
        if is_api:
            return JSONResponse(
                content={
                    "detail": f"Ошибка: {type(exc).__name__}: {exc}",
                    "traceback": str(exc),
                },
                status_code=500,
            )
        return HTMLResponse(
            content=f"<h1>Ошибка сервера</h1><pre>{exc}</pre>",
            status_code=500,
        )

    # Production: обобщённое сообщение + request_id для корреляции с логами
    if is_api:
        return JSONResponse(
            content={"detail": "Внутренняя ошибка сервера", "request_id": request_id},
            status_code=500,
            headers={"X-Request-ID": request_id},
        )
    return HTMLResponse(
        content=(
            "<h1>Ошибка сервера</h1>"
            "<p>Проверьте, что БД запущена: <code>docker-compose up -d</code></p>"
            f"<p>Код обращения для техподдержки: <code>{request_id}</code></p>"
        ),
        status_code=500,
        headers={"X-Request-ID": request_id},
    )


# Импорт роутеров
from web.routes import (
    admin_panel,
    auth,
    dashboard,
    departments,
    dept_frame,
    events,
    faq,
    knowledge_base,
    logs,
    tickets,
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(admin_panel.router, prefix="/admin/admins", tags=["admin"])
app.include_router(tickets.router, prefix="/tickets", tags=["tickets"])
app.include_router(knowledge_base.router, prefix="/knowledge", tags=["knowledge_base"])
app.include_router(faq.router, prefix="/faq", tags=["faq"])
app.include_router(events.router, prefix="/events", tags=["events"])
app.include_router(logs.router, prefix="/logs", tags=["logs"])
app.include_router(dashboard.router, tags=["dashboard"])
app.include_router(dept_frame.router, prefix="/dept", tags=["dept_frame"])
app.include_router(departments.router, prefix="/departments", tags=["departments"])


# Healthcheck для мониторинга и docker healthcheck
@app.get("/health")
async def health():
    """Проверка живости панели и доступности БД."""
    from sqlalchemy import text

    from core.database import engine

    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception:
        logger.exception("Healthcheck: БД недоступна")
        return JSONResponse(
            content={"status": "error", "detail": "database unavailable"},
            status_code=503,
        )
