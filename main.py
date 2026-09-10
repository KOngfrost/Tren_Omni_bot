import asyncio
import logging
import socket
import sys

import aiohttp
from vkbottle.exception_factory.base_exceptions import VKAPIError

from bots.vk.bot import vk_bot
from core.config import settings
from core.database import run_migrations
from core.heartbeat import touch_heartbeat
from core.logging_config import setup_logging
from core.reporting import start_report_scheduler
from core.sentry import init_sentry

logger = logging.getLogger(__name__)

# Настраиваем логирование при старте
setup_logging()
init_sentry("bot")

# Флаг, чтобы планировщик запускался только один раз
_scheduler_started = False
_scheduler_loop: asyncio.AbstractEventLoop | None = None

# Ссылки на фоновые задачи: без сильной ссылки GC может уничтожить задачу
_background_tasks: set[asyncio.Task] = set()


def _track_background_task(task: asyncio.Task) -> asyncio.Task:
    """Удерживать сильную ссылку на фоновую задачу до завершения."""
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _spawn_background_task(coro) -> asyncio.Task:
    """Создать фоновую задачу и удерживать ссылку на неё до завершения."""
    return _track_background_task(asyncio.create_task(coro))


def initialize_database() -> None:
    """Применяет Alembic-миграции.

    В production схему создаёт только Alembic; базу создаёт PostgreSQL-
    контейнер (POSTGRES_DB). Автосоздание базы приложением (ensure_database_exists)
    включается только в dev через ALLOW_DB_CREATE=true.
    """
    if settings.ALLOW_DB_CREATE:
        from core.database import ensure_database_exists

        asyncio.run(ensure_database_exists())
    run_migrations()


async def _start_scheduler() -> None:
    """Запускает asyncio-планировщик отчётов, outbox-воркер и heartbeat.

    Вызывается ОДИН раз при старте бота (через on_startup vkbottle).
    Флаг _scheduler_started защищает от повторного запуска при retry.
    """
    global _scheduler_started, _scheduler_loop
    current_loop = asyncio.get_running_loop()
    if _scheduler_started and _scheduler_loop is current_loop:
        return
    _scheduler_started = True
    _scheduler_loop = current_loop

    _track_background_task(start_report_scheduler(vk_bot.api))
    logger.info("Планировщик отчётов запущен")

    try:
        from core.ticket_service import sync_unassigned_ticket_departments

        await sync_unassigned_ticket_departments()
    except Exception as exc:
        logger.warning("Не удалось выполнить автопривязку отделов: %s", exc)

    # Outbox: доставка VK-уведомлений, записанных в ту же транзакцию,
    # что и изменения заявок (см. core/outbox.py).
    from core.outbox import outbox_worker_loop

    _spawn_background_task(outbox_worker_loop())

    # Периодическое обновление heartbeat для docker healthcheck
    async def _heartbeat_loop() -> None:
        while True:
            touch_heartbeat()
            await asyncio.sleep(60)

    _spawn_background_task(_heartbeat_loop())


async def run_vk_polling() -> None:
    retry_delay = 5

    while True:
        try:
            await _start_scheduler()
            await vk_bot.run_polling()
            logger.warning("VK polling stopped; retrying in %s seconds", retry_delay)
        except (aiohttp.ClientError, OSError, socket.gaierror, TimeoutError) as error:
            logger.error(
                "VK polling is unavailable: %s; retrying in %s seconds",
                error,
                retry_delay,
            )

        await asyncio.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 300)


def run() -> None:
    try:
        settings.ensure_production_config()
        initialize_database()
        asyncio.run(run_vk_polling())
    except RuntimeError as error:
        logger.error("Ошибка конфигурации: %s", error)
        print(f"Ошибка конфигурации: {error}", file=sys.stderr)
        sys.exit(1)
    except VKAPIError as error:
        if "longpoll" in str(error).lower():
            msg = (
                "VK Long Poll отключен. Включите его в настройках сообщества: "
                "Управление сообществом > Работа с API > Long Poll API. "
                "В разделе Long Poll API включите API и события сообщений, "
                "затем проверьте права токена сообщества."
            )
            logger.error(msg)
            print(msg, file=sys.stderr)
            return
        raise


if __name__ == "__main__":
    run()
