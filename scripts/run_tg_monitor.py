"""Точка входа: запуск Telegram-бота мониторинга сервера и фонового сервиса алертов."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
from pathlib import Path

# Обеспечиваем импорт модулей проекта
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bots.telegram.bot import create_telegram_bot, setup_bot_commands
from core.config import get_settings
from core.logging_config import setup_logging

logger = logging.getLogger("tg_monitor")


async def main() -> None:
    setup_logging()
    settings = get_settings()

    if not settings.TELEGRAM_BOT_TOKEN or settings.TELEGRAM_ADMIN_ID <= 0:
        logger.warning(
            "Telegram-мониторинг пропущен: TELEGRAM_BOT_TOKEN или TELEGRAM_ADMIN_ID не заданы в .env. "
            "Для включения мониторинга укажите токен бота от @BotFather и ваш числовой Telegram ID."
        )
        # Если запущено в контейнере, не уходим в бесконечный crash-loop, а ждем или спим
        while True:
            await asyncio.sleep(3600)

    logger.info(
        "Запуск Telegram-мониторинга для администратора ID=%d (интервал проверок=%dc)...",
        settings.TELEGRAM_ADMIN_ID,
        settings.TELEGRAM_CHECK_INTERVAL_SECONDS,
    )

    bot, dp, monitor_service = create_telegram_bot(settings)

    monitor_task = asyncio.create_task(monitor_service.start())

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Получен сигнал завершения работы...")
        monitor_service.stop()
        monitor_task.cancel()
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, _signal_handler)

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

        # Запуск polling бота
        await dp.start_polling(bot)
    except asyncio.CancelledError:
        logger.info("Polling Telegram-бота остановлен.")
    finally:
        monitor_service.stop()
        if not monitor_task.done():
            monitor_task.cancel()
        await bot.session.close()
        logger.info("Telegram-мониторинг корректно остановлен.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Процесс завершён пользователем.")
