"""
Главный модуль FastAPI-приложения.

Включает:
- CSRF-защита (CSRFMiddleware)
- Security Headers (X-Frame-Options, CSP, X-Content-Type-Options и др.)
- Валидация размера запросов
- Безопасный глобальный обработчик ошибок
- Кэширование department_name для middleware
- Логирование в файлы (core/logging_config.py)
"""

import asyncio
import contextlib
import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware

from core.config import settings
from core.database import async_session_maker

# Настраиваем логирование при старте веб-панели
from core.logging_config import setup_logging
from core.sentry import init_sentry
from web.security.csrf import CSRFMiddleware
from web.security.middleware import (
    RequestSizeLimitMiddleware,
    RequestSizeValidator,
    SecurityHeadersMiddleware,
)
from web.security.session_store import RedisSessionMiddleware
from web.templating import templates

setup_logging(log_dir=os.path.join(os.path.dirname(__file__), "..", "logs"))
init_sentry("web-admin")

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Жизненный цикл: фоновые задачи веб-панели.

    Outbox-воркер запускается и в панели, чтобы доставка VK-уведомлений

    не останавливалась при перезапуске бота. При WEB_WORKERS>1 фактическим
    исполнителем становится один процесс (Redis distributed lock,
    см. core/task_dispatcher.py). Отключается переменной WEB_OUTBOX_WORKER=false.

    Первым делом выполняется страж старта (core.startup_guard): в production
    он прерывает запуск при placeholder-секретах и небезопасной конфигурации.
    """
    from core.startup_guard import enforce_startup_security

    enforce_startup_security(settings, component="web-admin")

    worker_task: asyncio.Task[None] | None = None

    if settings.WEB_OUTBOX_WORKER:
        from core.outbox import outbox_worker_loop

        worker_task = asyncio.create_task(outbox_worker_loop())
        logger.info("Outbox-воркер веб-панели запущен")
    cleanup_task: asyncio.Task[None] | None = None
    try:
        from core.ticket_service import sync_unassigned_ticket_departments

        await sync_unassigned_ticket_departments()
    except Exception as exc:
        logger.warning("Не удалось выполнить автопривязку отделов: %s", exc)
    # Фоновая очистка устаревших записей rate-limit (crud_attempts, login_attempts)
    # — вынесена из горячего пути DBRateLimiter (Ошибка #10).
    from core.rate_limit_cleanup import start_rate_limit_cleanup

    cleanup_task = start_rate_limit_cleanup()
    logger.info("Фоновая очистка rate-limit журналов запущена")
    yield
    if cleanup_task is not None:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        logger.info("Фоновая очистка rate-limit журналов остановлена")
    if worker_task is not None:
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task
        logger.info("Outbox-воркер веб-панели остановлен")
    from core.redis_client import close_redis_client

    await close_redis_client()


app = FastAPI(
    title="oss-web-panel",
    description="Веб-админка OSS Bot: управление обращениями студентов",
    version=settings.PROJECT_VERSION,
    lifespan=lifespan,
)

# === Prometheus Метрики ===
try:
    from prometheus_fastapi_instrumentator import Instrumentator

    instrumentator = Instrumentator(
        should_group_status_codes=False,
        excluded_handlers=[".*admin/health", "/health", "/metrics"],
    )
    instrumentator.instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
except Exception as e:
    logger.warning("Не удалось настроить Prometheus Instrumentator: %s", e)

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

# 3. Redis-backed сессия с безопасными настройками и TTL (для CSRF и auth)
# ДОЛЖЕН выполняться ДО CSRFMiddleware,
# чтобы scope["session"] был создан до того, как он попытается его прочитать.
_session_secret = settings.session_secret_key
if not _session_secret:
    raise RuntimeError(
        "SESSION_SECRET_KEY не задан. Установите его в .env и перезапустите панель. "
        'Генерация: python -c "import secrets; print(secrets.token_urlsafe(64))"'
    )

# Жёсткие проверки для production (дефолтные креды БД, слабый секрет и т.п.)
settings.ensure_production_config()

app.add_middleware(
    RedisSessionMiddleware,
    secret_key=_session_secret,
    max_age=settings.SESSION_TTL,
    https_only=settings.SESSION_HTTPS_ONLY,
    same_site=settings.SESSION_SAME_SITE,
    path="/",
)

# 4. Сжатие ответов GZip для оптимизации производительности (размер от 1 КБ)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# 4.1 Жёсткий лимит размера тела запроса (фактические байты потока).
# Нужен потому, что запрос с Transfer-Encoding: chunked приходит без
# Content-Length и обходит проверку заголовка. Выполняется раньше CSRF и
# сессии, но внутри SecurityHeadersMiddleware — чтобы отказ 413 тоже
# получал безопасные заголовки.
_REQUEST_MAX_BODY_SIZE = 10 * 1024 * 1024
app.add_middleware(RequestSizeLimitMiddleware, max_body_size=_REQUEST_MAX_BODY_SIZE)


# 5. Security Headers — X-Frame-Options, CSP, X-Content-Type-Options и др.
# (outermost - выполняется первым)
app.add_middleware(SecurityHeadersMiddleware)


# Static
app.mount("/static", StaticFiles(directory="web/static"), name="static")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_endpoint():
    """Отдача иконки favicon.ico."""
    return FileResponse("web/static/favicon.ico", media_type="image/x-icon")


# Middleware для кэширования статических файлов (Cache-Control)
@app.middleware("http")
async def add_static_cache_headers(request: Request, call_next):
    """Добавляет Cache-Control заголовок для статики."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=86400, immutable"
    return response


# === Константы путей для пропуска middleware ===
_SKIP_MIDDLEWARE_PREFIXES = (
    "/static/",
    "/health",
    "/auth/",
    "/legal/",
    "/favicon.ico",
)

# Валидатор размера запроса (10MB лимит)
_REQUEST_MAX_BODY_SIZE = 10 * 1024 * 1024
_request_size_validator = RequestSizeValidator(max_body_size=_REQUEST_MAX_BODY_SIZE)


# Middleware для валидации размера запросов
@app.middleware("http")
async def validate_request_size(request: Request, call_next):
    """Проверяет объявленный размер запроса (Content-Length) до его обработки."""
    await _request_size_validator.check_form_size(request)
    response = await call_next(request)
    return response


# === Кэширование department_name ===

from types import SimpleNamespace

from core.cache import cache_get, cache_set


# Middleware для загрузки department_name в контекст шаблонов
@app.middleware("http")
async def add_department_name(request: Request, call_next):
    """Загружает department_name и список отделов пользователя из БД (с кэшем)."""
    session = request.scope.get("session", {})
    request.state.session_id = session.get("session_id")
    # Пропускаем статические файлы, healthcheck, auth и API-запросы
    if any(
        request.url.path.startswith(prefix) for prefix in _SKIP_MIDDLEWARE_PREFIXES
    ) or "application/json" in request.headers.get("accept", ""):
        return await call_next(request)

    try:
        user = session.get("user")
        if user:
            from web.dependencies import get_admin_scope, get_departments_for_user

            web_user_id = user.get("web_user_id")
            # Попытка получить из распределённого кэша
            cached = await cache_get(f"dept_ctx:{web_user_id}") if web_user_id else None
            if cached is not None:
                department_name = cached.get("department_name")
                user_departments = [
                    SimpleNamespace(id=d["id"], name=d["name"])
                    for d in cached.get("departments", [])
                ]
            else:
                async with async_session_maker() as db_session:
                    user_departments = await get_departments_for_user(db_session, user)
                    is_super, _dept_id = await get_admin_scope(db_session, user)

                department_name = (
                    user_departments[0].name if not is_super and user_departments else None
                )

                # Сохраняем в распределённый кэш
                if web_user_id:
                    await cache_set(
                        f"dept_ctx:{web_user_id}",
                        {
                            "department_name": department_name,
                            "departments": [
                                {"id": d.id, "name": d.name} for d in user_departments
                            ],
                        },
                        ttl=300,
                    )

            # The selected department belongs to the current URL, not the user.
            dept_id = None
            if request.url.path.startswith("/dept/"):
                parts = request.url.path.strip("/").split("/")
                if len(parts) >= 2 and parts[1].isdigit():
                    dept_id = int(parts[1])

            request.state.department_name = department_name
            request.state.user_departments = user_departments
            request.state.dept_id = dept_id
    except Exception:
        # Если не удалось загрузить — продолжаем без department_name
        logger.debug("Не удалось загрузить department_name для middleware", exc_info=True)

    response = await call_next(request)
    return response


# === Глобальный обработчик ошибок — без раскрытия деталей ===


def _is_browser_request(request: Request) -> bool:
    """Проверить, является ли запрос браузерным (не API)."""
    accept = request.headers.get("accept", "")
    return "application/json" not in accept


def _get_error_page_context(
    request: Request,
    status: int,
    error_id: str | None = None,
) -> dict:
    """Получить контекст для страницы ошибки.

    Обязательно включает ``request`` — starlette 0.38+ требует его в контексте
    шаблона, иначе TemplateResponse бросает ValueError.
    """
    messages = {
        400: (
            "Неверный запрос",
            "Пожалуйста, проверьте введённые данные и попробуйте снова.",
            "🔍",
            True,
            True,
            False,
        ),
        403: (
            "Доступ запрещён",
            "У вас нет прав для доступа к этой странице. Обратитесь к суперадминистратору.",
            "🚫",
            True,
            True,
            False,
        ),
        404: (
            "Страница не найдена",
            "Запрошенная страница не существует или была перемещена.",
            "📄",
            True,
            True,
            True,
        ),
        405: (
            "Метод не разрешён",
            "Запрашиваемый метод HTTP не поддерживается для этой страницы.",
            "🚫",
            True,
            True,
            False,
        ),
        413: (
            "Файл слишком большой",
            "Размер запроса превышает допустимый лимит. Попробуйте загрузить файл поменьше.",
            "📦",
            True,
            True,
            False,
        ),
        422: (
            "Некорректные данные",
            "Проверьте правильность заполнения формы и попробуйте снова.",
            "📝",
            True,
            True,
            False,
        ),
        429: (
            "Слишком много запросов",
            "Вы сделали слишком много запросов. Подождите минуту и попробуйте снова.",
            "⏳",
            True,
            True,
            False,
        ),
        500: (
            "Внутренняя ошибка сервера",
            "Произошла ошибка. Пожалуйста, сфотографируйте экран и отправьте техническому администратору.",
            "⚠️",
            True,
            True,
            True,
        ),
    }
    if status in messages:
        title, msg, icon, refresh, back, home = messages[status]
    else:
        title, msg, icon, refresh, back, home = (
            f"Ошибка {status}",
            "Произошла ошибка. Пожалуйста, сфотографируйте экран и отправьте техническому администратору.",
            "⚠️",
            True,
            True,
            True,
        )
    context = {
        "request": request,
        "error_code": str(status),
        "error_title": title,
        "error_message": msg,
        "error_icon": icon,
        "show_refresh": refresh,
        "show_back": back,
        "show_home": home,
    }
    if error_id:
        context["error_id"] = error_id
    return context


@app.exception_handler(StarletteHTTPException)
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException | HTTPException):
    """Сохранить корректный HTTP-статус для отказов auth и CSRF.

    Регистрируется и для StarletteHTTPException (ловит 404 на неизвестных
    маршрутах), и для HTTPException. Для браузерных запросов возвращает
    стилизованную страницу error.html, для API — JSON.
    """
    if exc.status_code in (302, 303) and exc.headers:
        return RedirectResponse(
            url=exc.headers.get("Location", "/auth/login"),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    # Для браузерных запросов возвращаем HTML-страницу ошибки
    if _is_browser_request(request):
        context = _get_error_page_context(request, exc.status_code)
        return templates.TemplateResponse(
            "error.html",
            context,
            status_code=exc.status_code,
        )

    # Для API-запросов возвращаем JSON
    return JSONResponse(
        content={"detail": exc.detail},
        status_code=exc.status_code,
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """422 ошибка валидации: для браузера — страница ошибки, для API — JSON."""
    if _is_browser_request(request):
        context = _get_error_page_context(request, 422)
        return templates.TemplateResponse(
            "error.html",
            context,
            status_code=422,
        )
    return JSONResponse(
        content={"detail": exc.errors()},
        status_code=422,
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Обрабатывает все необработанные исключения БЕЗ раскрытия деталей.

    В dev-режиме возвращает traceback для удобства отладки.
    В production — стилизованная страница error.html + код обращения.
    """
    request_id = uuid.uuid4().hex[:12]
    logger.exception(
        "Необработанное исключение на %s [request_id=%s]", request.url.path, request_id
    )

    is_api = (
        request.url.path.startswith("/tickets/")
        or request.url.path.startswith("/api/")
        or "application/json" in request.headers.get("accept", "")
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
            content={
                "detail": "Произошла ошибка. Пожалуйста, сфотографируйте экран и отправьте техническому администратору.",
                "request_id": request_id,
            },
            status_code=500,
            headers={"X-Request-ID": request_id},
        )
    context = _get_error_page_context(request, 500, error_id=request_id)
    return templates.TemplateResponse(
        "error.html",
        context,
        status_code=500,
        headers={"X-Request-ID": request_id},
    )


# Импорт роутеров
from web.routes import (
    admin_panel,
    api,
    api_v1,
    auth,
    dashboard,
    departments,
    dept_frame,
    events,
    faq,
    knowledge_base,
    legal,
    logs,
    tickets,
    vk_callback,
)
from web.routes import settings as settings_route

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(legal.router, prefix="/legal", tags=["legal"])
app.include_router(admin_panel.router, prefix="/admin/admins", tags=["admin"])
app.include_router(tickets.router, prefix="/tickets", tags=["tickets"])
app.include_router(knowledge_base.router, prefix="/knowledge", tags=["knowledge_base"])
app.include_router(faq.router, prefix="/faq", tags=["faq"])
app.include_router(events.router, prefix="/events", tags=["events"])
app.include_router(logs.router, prefix="/logs", tags=["logs"])
app.include_router(settings_route.router, prefix="/settings", tags=["settings"])
app.include_router(dashboard.router, tags=["dashboard"])
app.include_router(dept_frame.router, prefix="/dept", tags=["dept_frame"])
app.include_router(departments.router, prefix="/departments", tags=["departments"])
app.include_router(api_v1.router, prefix="/api", tags=["api_v1"])
app.include_router(api.router, prefix="/api", tags=["api"])
app.include_router(vk_callback.router)


@app.get("/health")
async def health():
    """Healthcheck: доступность БД, синхронизация времени, bootstrap-режим.

    `bootstrap_mode` (раздел 5 ТЗ) — временный доступ по учётным данным из .env
    активен, пока в базе нет постоянного суперадминистратора. Поле обязательно
    видно мониторингу, чтобы bootstrap не остался в production незамеченным.
    """
    from sqlalchemy import text

    from core.bootstrap_guard import bootstrap_mode_status
    from core.database import engine
    from core.time_utils import check_time_sync

    if engine is None:
        return JSONResponse(
            content={"status": "error", "detail": "database engine not configured"},
            status_code=503,
        )

    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
            time_sync = await check_time_sync(conn)

        bootstrap: dict[str, object] | None
        try:
            async with async_session_maker() as session:
                bootstrap = await bootstrap_mode_status(session, settings)
        except Exception as exc:
            # Fail-soft только для диагностического поля: сам healthcheck остаётся ok
            logger.warning("Healthcheck: не удалось определить bootstrap-режим: %s", exc)
            bootstrap = None

        return {
            "status": "ok",
            "time_sync": time_sync,
            "bootstrap_mode": None if bootstrap is None else bootstrap["bootstrap_mode"],
            "bootstrap": bootstrap,
        }
    except Exception:
        logger.exception("Healthcheck: БД недоступна")
        return JSONResponse(
            content={"status": "error", "detail": "database unavailable"},
            status_code=503,
        )
