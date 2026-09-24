"""Telegram-бот для мониторинга сервера, управления контейнерами и Telegram Mini App."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    CallbackQuery,
    MenuButtonCommands,
    MenuButtonWebApp,
    Message,
    TelegramObject,
    WebAppInfo,
)

from bots.telegram.docker_client import DockerClient
from bots.telegram.handlers import get_handlers_router
from bots.telegram.handlers.backup import (
    cmd_backup,
    perform_database_backup,
    rotate_old_backups,
)
from bots.telegram.handlers.maintenance import render_maintenance_content
from bots.telegram.handlers.status import render_status_content
from bots.telegram.handlers.two_factor import render_two_factor_content
from bots.telegram.keyboards import get_main_reply_keyboard
from bots.telegram.monitor_service import MonitorService
from core.config import Settings

logger = logging.getLogger(__name__)

# Экспортируем роутер для совместимости с тестами и модулями
router = get_handlers_router()


class AdminAccessMiddleware(BaseMiddleware):
    """Middleware для ограничения доступа: бот отвечает ТОЛЬКО указанному глав-админу."""

    def __init__(
        self,
        admin_id: int,
        docker_client: DockerClient,
        settings: Settings | None = None,
    ):
        self.admin_id = admin_id
        self.docker_client = docker_client
        self.settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return None

        # Проверка соответствия Telegram ID
        if self.admin_id <= 0 or user.id != self.admin_id:
            logger.warning("Отклонён запрос от неавторизованного пользователя Telegram ID=%s", user.id)
            if isinstance(event, Message):
                await event.answer("⛔ Доступ запрещён. Этот бот доступен только администратору сервера.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Доступ запрещён.", show_alert=True)
            return None

        data["docker_client"] = self.docker_client
        if self.settings is not None:
            data["settings"] = self.settings
        else:
            from core.config import get_settings

            data["settings"] = get_settings()
        return await handler(event, data)


async def setup_bot_commands(
    bot: Bot,
    admin_id: int,
    webapp_url: str | None = None,
) -> None:
    """Очистить устаревшие команды и зарегистрировать аккуратное меню команд через слэш.

    Также настраивает кнопку постоянного меню чата (Menu Button) для мгновенного запуска Mini App.
    """
    try:
        # 1. Сброс старых/глобальных команд из кэша Telegram
        try:
            await bot.delete_my_commands(scope=BotCommandScopeDefault())
            if admin_id > 0:
                await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception as e:
            logger.debug("Сброс старых команд: %s", e)

        # 2. Установка чистого, лаконичного списка слэш-команд
        clean_commands = [
            BotCommand(command="status", description="📊 Состояние сервера и контейнеров"),
            BotCommand(command="panel", description="📱 Открыть веб-панель (Mini App)"),
            BotCommand(command="restart", description="🔄 Перезапуск контейнеров и сервисов"),
            BotCommand(command="logs", description="📋 Просмотр свежих логов контейнера"),
            BotCommand(command="backup", description="💾 Резервная копия базы данных"),
            BotCommand(command="maintenance", description="🚧 Режим технических работ (503)"),
            BotCommand(command="2fa", description="🔐 Управление двухфакторной аутентификацией"),
            BotCommand(command="help", description="ℹ️ Справка и документация"),
        ]

        if admin_id > 0:
            await bot.set_my_commands(
                commands=clean_commands,
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
            logger.info("Зарегистрировано %d слэш-команд для администратора ID=%d", len(clean_commands), admin_id)
        else:
            await bot.set_my_commands(commands=clean_commands, scope=BotCommandScopeDefault())

        # 3. Настройка кнопки постоянного меню (Menu Button) рядом со строкой ввода
        if admin_id > 0:
            if webapp_url and webapp_url.startswith("https://"):
                await bot.set_chat_menu_button(
                    chat_id=admin_id,
                    menu_button=MenuButtonWebApp(
                        text="📱 Веб-панель",
                        web_app=WebAppInfo(url=webapp_url),
                    ),
                )
                logger.info("Установлена кнопка Mini App в чате Telegram: %s", webapp_url)
            else:
                await bot.set_chat_menu_button(
                    chat_id=admin_id,
                    menu_button=MenuButtonCommands(),
                )
    except Exception as exc:
        logger.warning("Не удалось настроить команды бота через Telegram API: %s", exc)


def create_telegram_bot(
    settings: Settings,
    docker_client: DockerClient | None = None,
) -> tuple[Bot, Dispatcher, MonitorService]:
    """Создать и настроить Telegram-бота мониторинга."""
    if not settings.TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан в конфигурации.")
    if settings.TELEGRAM_ADMIN_ID <= 0:
        raise ValueError("TELEGRAM_ADMIN_ID не задан или некорректен.")

    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    if docker_client is None:
        docker_client = DockerClient()

    middleware = AdminAccessMiddleware(settings.TELEGRAM_ADMIN_ID, docker_client, settings=settings)
    router.message.middleware(middleware)
    router.callback_query.middleware(middleware)

    dp.include_router(router)

    monitor_service = MonitorService(
        bot=bot,
        admin_id=settings.TELEGRAM_ADMIN_ID,
        docker_client=docker_client,
        check_interval=settings.TELEGRAM_CHECK_INTERVAL_SECONDS,
        alerts_enabled=settings.TELEGRAM_ALERTS_ENABLED,
        settings=settings,
    )

    return bot, dp, monitor_service


__all__ = [
    "AdminAccessMiddleware",
    "cmd_backup",
    "create_telegram_bot",
    "get_main_reply_keyboard",
    "perform_database_backup",
    "render_maintenance_content",
    "render_status_content",
    "render_two_factor_content",
    "rotate_old_backups",
    "router",
    "setup_bot_commands",
]
