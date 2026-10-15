"""Точка входа: запуск Telegram-бота мониторинга сервера и фонового сервиса алертов."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

# Обеспечиваем импорт модулей проекта
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bots.telegram.bot import create_telegram_bot, setup_bot_commands
from core.config import get_settings
from core.logging_config import setup_logging

logger = logging.getLogger("tg_monitor")


async def _wait_for_idle_signal() -> None:
    idle_stop_event = asyncio.Event()
    idle_loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            idle_loop.add_signal_handler(sig, idle_stop_event.set)
        except (NotImplementedError, RuntimeError):
            with contextlib.suppress(Exception):
                signal.signal(sig, lambda *_: idle_loop.call_soon_threadsafe(idle_stop_event.set))
    await idle_stop_event.wait()


def _setup_shutdown_handlers(shutdown_coro: Callable[[], Coroutine[Any, Any, None]]) -> None:
    loop = asyncio.get_running_loop()
    shutdown_task: asyncio.Task[None] | None = None

    def _signal_handler() -> None:
        nonlocal shutdown_task
        if shutdown_task is None or shutdown_task.done():
            shutdown_task = asyncio.create_task(shutdown_coro())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except (NotImplementedError, RuntimeError):
            with contextlib.suppress(Exception):
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(_signal_handler))


async def main() -> None:
    setup_logging()
    settings = get_settings()

    if not settings.TELEGRAM_BOT_TOKEN or settings.TELEGRAM_ADMIN_ID <= 0:
        logger.warning(
            "Telegram-мониторинг пропущен: TELEGRAM_BOT_TOKEN или TELEGRAM_ADMIN_ID не заданы в .env. "
            "Для включения мониторинга укажите токен бота от @BotFather и ваш числовой Telegram ID."
        )
        await _wait_for_idle_signal()
        return

    logger.info(
        "Запуск Telegram-мониторинга для администратора ID=%d (интервал проверок=%dc)...",
        settings.TELEGRAM_ADMIN_ID,
        settings.TELEGRAM_CHECK_INTERVAL_SECONDS,
    )

    bot, dp, monitor_service = create_telegram_bot(settings)
    monitor_task = asyncio.create_task(monitor_service.start())
    stop_event = asyncio.Event()

    async def _shutdown() -> None:
        logger.info("Получен сигнал завершения работы, останавливаем сервисы и polling...")
        monitor_service.stop()
        if not monitor_task.done():
            monitor_task.cancel()
        stop_event.set()
        with contextlib.suppress(Exception):
            await dp.stop_polling()

    _setup_shutdown_handlers(_shutdown)

    try:
        # Регистрируем аккуратный список слэш-команд и кнопку WebApp в чате
        await setup_bot_commands(bot, settings.TELEGRAM_ADMIN_ID, settings.TELEGRAM_WEBAPP_URL)

        # Отправляем сообщение о старте монитора администратору
        try:
            await bot.send_message(
                chat_id=settings.TELEGRAM_ADMIN_ID,
                text="🟢 <b>Telegram-монитор сервера запущен и активен!</b>\nИспользуйте /status или кнопку «📱 Веб-панель» для работы.",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("Не удалось отправить приветственное сообщение глав-админу: %s", e)

        # Запуск polling бота (handle_signals=False, чтобы наш _signal_handler вызывал dp.stop_polling)
        if not stop_event.is_set():
            await dp.start_polling(bot, handle_signals=False)
    except asyncio.CancelledError:
        logger.info("Polling Telegram-бота остановлен.")
    finally:
        monitor_service.stop()
        if not monitor_task.done():
            monitor_task.cancel()
        with contextlib.suppress(Exception):
            await monitor_task
        with contextlib.suppress(Exception):
            await bot.session.close()
        logger.info("Telegram-мониторинг корректно остановлен.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        logger.info("Процесс завершён пользователем.")
