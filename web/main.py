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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from core.config import settings

# Настраиваем логирование при старте веб-панели
from core.logging_config import setup_logging
from core.sentry import init_sentry
from web.exception_handlers import register_exception_handlers
from web.middleware_setup import setup_middleware

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

    # Би-недельная архивация старых логов и завершённых заявок (gzip → archives/)
    from core.archival import start_archival

    archival_task: asyncio.Task[None] = start_archival()
    logger.info("Фоновая архивация логов и заявок запущена")

    # Автоудаление истёкших временных учётных записей. Первая чистка
    # выполняется сразу при старте, дальше — по таймеру.
    temp_admin_task: asyncio.Task[None] | None = None
    try:
        from core.temp_admin_cleanup import (
            cleanup_expired_temporary_admins,
            temp_admin_cleanup_loop,
        )

        await cleanup_expired_temporary_admins()
        temp_admin_task = asyncio.create_task(temp_admin_cleanup_loop())
        logger.info("Автоудаление истёкших временных администраторов запущено")
    except Exception as exc:
        logger.warning("Не удалось запустить очистку временных админов: %s", exc)

    # Выгрузка буфера аудита в БД: события копятся в памяти во время
    # обработки запросов, а записываются пакетами из этого цикла.
    from core.audit import audit_flush_loop

    audit_task: asyncio.Task[None] = asyncio.create_task(audit_flush_loop())
    logger.info("Выгрузка журнала аудита запущена")

    # В callback-режиме FSM-состояния студентов ведутся в этом процессе.
    # При недоступном Redis они копятся в словаре-фоллбеке диспенсера,
    # поэтому их TTL-сборщик запускается здесь же (core/state_dispenser.py).
    state_maintenance_task: asyncio.Task[None] | None = None
    if settings.VK_MODE == "callback":
        from bots.vk.bot import vk_bot

        state_maintenance_task = vk_bot.state_dispenser.start_maintenance()
        logger.info("Периодическая очистка FSM-состояний запущена")

    from core.events import register_ticket_change_listener, unregister_ticket_change_listener
    from web.routes.sse import (
        start_sse_redis_listener,
        stop_sse_redis_listener,
        trigger_sse_update,
    )

    register_ticket_change_listener(trigger_sse_update)
    sse_redis_task = start_sse_redis_listener()
    yield

    unregister_ticket_change_listener(trigger_sse_update)
    await stop_sse_redis_listener(sse_redis_task)

    # Дописываем накопленные события аудита перед остановкой, иначе
    # последние действия не попадут в журнал.
    try:
        from core.audit import flush_audit_queue

        audit_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await audit_task
        await flush_audit_queue()
        logger.info("Выгрузка журнала аудита остановлена")
    except Exception as exc:
        logger.debug("Не удалось дописать журнал аудита при остановке: %s", exc)

    if temp_admin_task is not None:
        temp_admin_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await temp_admin_task
        logger.info("Автоудаление истёкших временных администраторов остановлено")
    if state_maintenance_task is not None:
        state_maintenance_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await state_maintenance_task
        logger.info("Периодическая очистка FSM-состояний остановлена")
    if cleanup_task is not None:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        logger.info("Фоновая очистка rate-limit журналов остановлена")
    archival_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await archival_task
    logger.info("Фоновая архивация логов и заявок остановлена")
    if worker_task is not None:
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task
        logger.info("Outbox-воркер веб-панели остановлен")
    from core.redis_client import close_redis_client

    await close_redis_client()

    from core.database import dispose_engine

    await dispose_engine()
    logger.info("Пул соединений с БД веб-панели корректно закрыт")


app = FastAPI(
    title="oss-web-panel",
    description="Веб-админка OSS Bot: управление обращениями студентов",
    version=settings.PROJECT_VERSION,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
register_exception_handlers(app)
setup_middleware(app)

from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from web.dependencies import require_auth

if settings.ENABLE_API_DOCS or not settings.IS_PRODUCTION:

    @app.get("/api/openapi.json", include_in_schema=False)
    async def get_open_api_endpoint(user: dict = Depends(require_auth)):
        return JSONResponse(get_openapi(title=app.title, version=app.version, routes=app.routes))

    @app.get("/api/docs", include_in_schema=False)
    async def get_documentation(user: dict = Depends(require_auth)):
        return get_swagger_ui_html(
            openapi_url="/api/openapi.json", title=f"{app.title} - Swagger UI"
        )

    @app.get("/api/redoc", include_in_schema=False)
    async def get_redoc_documentation(user: dict = Depends(require_auth)):
        return get_redoc_html(openapi_url="/api/openapi.json", title=f"{app.title} - ReDoc")


# Static
app.mount("/static", StaticFiles(directory="web/static"), name="static")


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
    partnerships,
    sse,
    system,
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
app.include_router(partnerships.router, prefix="/partnerships", tags=["partnerships"])
app.include_router(logs.router, prefix="/logs", tags=["logs"])
app.include_router(settings_route.router, prefix="/settings", tags=["settings"])
app.include_router(dashboard.router, tags=["dashboard"])
app.include_router(dept_frame.router, prefix="/dept", tags=["dept_frame"])
app.include_router(departments.router, prefix="/departments", tags=["departments"])
app.include_router(api_v1.router, prefix="/api", tags=["api_v1"])
app.include_router(api.router, prefix="/api", tags=["api"])
app.include_router(sse.router, prefix="/api", tags=["sse"])
app.add_api_websocket_route("/ws", sse.websocket_stream)
app.include_router(vk_callback.router)
app.include_router(system.router)
