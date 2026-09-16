"""Telegram-бот для мониторинга сервера и управления контейнерами."""

from __future__ import annotations

import html
import logging
import os
import subprocess
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    TelegramObject,
)

from bots.telegram.docker_client import DockerClient
from bots.telegram.monitor_service import MonitorService
from bots.telegram.system_metrics import format_metrics_message, get_system_metrics
from core.config import Settings

logger = logging.getLogger(__name__)


class AdminAccessMiddleware(BaseMiddleware):
    """Middleware для ограничения доступа: бот отвечает ТОЛЬКО указанному глав-админу."""

    def __init__(self, admin_id: int, docker_client: DockerClient):
        self.admin_id = admin_id
        self.docker_client = docker_client

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
        return await handler(event, data)


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Главная клавиатура команд."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статус"), KeyboardButton(text="🔄 Перезапуск")],
            [KeyboardButton(text="📋 Логи"), KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
    )


async def render_status_content(docker_client: DockerClient) -> tuple[str, InlineKeyboardMarkup]:
    """Сформировать текст и инлайн-клавиатуру статуса сервера."""
    metrics = get_system_metrics()
    metrics_text = format_metrics_message(metrics)

    containers = await docker_client.list_containers(all=True)

    lines = [metrics_text, "", "📦 <b>Контейнеры Docker:</b>"]
    if not containers:
        lines.append("<i>Контейнеры не найдены или Docker недоступен</i>")
    else:
        for c in containers:
            health_part = f" ({c.health})" if c.health else ""
            lines.append(f"{c.status_emoji} <code>{c.name}</code> — {c.status}{health_part}")

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="status:refresh"),
                InlineKeyboardButton(text="🔄 Перезапуск", callback_data="menu:restart"),
            ],
            [
                InlineKeyboardButton(text="📋 Логи", callback_data="menu:logs"),
            ],
        ]
    )

    return "\n".join(lines), keyboard


router = Router()


@router.message(CommandStart())
@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def cmd_start_help(message: Message) -> None:
    """Приветствие и справка по командам."""
    text = (
        "👋 <b>Панель мониторинга сервера и управления контейнерами</b>\n\n"
        "Доступные команды:\n"
        "📊 <b>/status</b> — Ресурсы сервера и состояние Docker-контейнеров\n"
        "🔄 <b>/restart</b> — Перезапуск отдельных сервисов или всего стека\n"
        "📋 <b>/logs</b> — Просмотр свежих логов контейнера\n"
        "⚠️ <b>/reboot</b> — Перезагрузка сервера (с подтверждением)\n\n"
        "При любых сбоях (падение контейнера, переход в <i>unhealthy</i>) "
        "бот автоматически уведомит вас тревожным сообщением."
    )
    await message.answer(text, reply_markup=get_main_reply_keyboard(), parse_mode="HTML")


@router.message(Command("status"))
@router.message(F.text == "📊 Статус")
async def cmd_status(message: Message, docker_client: DockerClient) -> None:
    """Команда вывода статуса системы."""
    text, keyboard = await render_status_content(docker_client)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "status:refresh")
async def callback_status_refresh(callback: CallbackQuery, docker_client: DockerClient) -> None:
    """Обновление карточки статуса системы."""
    text, keyboard = await render_status_content(docker_client)
    try:
        if callback.message:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer("Статус обновлён.")
    except Exception:
        await callback.answer("Данные актуальны.")


@router.message(Command("restart"))
@router.message(F.text == "🔄 Перезапуск")
@router.callback_query(F.data == "menu:restart")
async def cmd_restart_menu(event: Message | CallbackQuery, docker_client: DockerClient) -> None:
    """Меню выбора контейнера для перезапуска."""
    containers = await docker_client.list_containers(all=True)
    sorted_c = sorted(containers, key=lambda x: (not x.is_project_container, x.name))

    keyboard_buttons = [
        [
            InlineKeyboardButton(
                text=f"{c.status_emoji} {c.name}",
                callback_data=f"restart:{c.name}",
            )
        ]
        for c in sorted_c
    ]

    keyboard_buttons.append(
        [InlineKeyboardButton(text="♻️ Перезапустить все сервисы", callback_data="restart:all_project")]
    )
    keyboard_buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="status:refresh")])

    text = "🔄 <b>Выберите контейнер для перезапуска:</b>"
    markup = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    if isinstance(event, CallbackQuery):
        if event.message:
            await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data == "restart:all_project")
async def callback_restart_all(callback: CallbackQuery, docker_client: DockerClient) -> None:
    """Перезапуск всех проектных контейнеров."""
    await callback.answer("Перезапуск проектных сервисов...")
    if callback.message:
        await callback.message.edit_text("⏳ <b>Перезапуск проектных сервисов...</b>", parse_mode="HTML")

    containers = await docker_client.list_containers(all=True)
    restarted = []
    for c in containers:
        if c.is_project_container and "migrate" not in c.name:
            ok, _ = await docker_client.restart_container(c.name)
            if ok:
                restarted.append(c.name)

    result_text = f"✅ Перезапущены: {', '.join(restarted)}" if restarted else "⚠️ Нет сервисов для перезапуска."
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📊 К статусу", callback_data="status:refresh")]]
    )
    if callback.message:
        await callback.message.edit_text(result_text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("restart:"))
async def callback_restart_single(callback: CallbackQuery, docker_client: DockerClient) -> None:
    """Перезапуск одного конкретного контейнера."""
    target = callback.data.split(":", 1)[1] if callback.data else ""
    if not target or target == "all_project":
        return

    await callback.answer(f"Перезапуск {target}...")
    if callback.message:
        await callback.message.edit_text(f"⏳ <b>Перезапуск контейнера <code>{target}</code>...</b>", parse_mode="HTML")

    ok, msg = await docker_client.restart_container(target)
    emoji = "✅" if ok else "❌"
    res_text = f"{emoji} <b>{msg}</b>"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 К выбору перезапуска", callback_data="menu:restart")],
            [InlineKeyboardButton(text="📊 К статусу", callback_data="status:refresh")],
        ]
    )
    if callback.message:
        await callback.message.edit_text(res_text, reply_markup=kb, parse_mode="HTML")


@router.message(Command("logs"))
@router.message(F.text == "📋 Логи")
@router.callback_query(F.data == "menu:logs")
async def cmd_logs_menu(event: Message | CallbackQuery, docker_client: DockerClient) -> None:
    """Меню выбора контейнера для просмотра логов."""
    containers = await docker_client.list_containers(all=True)
    sorted_c = sorted(containers, key=lambda x: (not x.is_project_container, x.name))

    keyboard_buttons = [
        [
            InlineKeyboardButton(
                text=f"📋 {c.name}",
                callback_data=f"logs:{c.name}",
            )
        ]
        for c in sorted_c
    ]
    keyboard_buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="status:refresh")])
    markup = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    text = "📋 <b>Выберите контейнер для просмотра логов:</b>"

    if isinstance(event, CallbackQuery):
        if event.message:
            await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data.startswith("logs:"))
async def callback_view_logs(callback: CallbackQuery, docker_client: DockerClient) -> None:
    """Вывод последних строк лога контейнера."""
    target = callback.data.split(":", 1)[1] if callback.data else ""
    if not target:
        await callback.answer("Не указан контейнер.")
        return

    await callback.answer("Загрузка логов...")
    raw_logs = await docker_client.get_container_logs(target, tail=35)
    safe_logs = html.escape(raw_logs[-3500:])

    text = f"📋 <b>Последние логи: <code>{target}</code></b>\n\n<pre><code>{safe_logs}</code></pre>"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить логи", callback_data=f"logs:{target}"),
                InlineKeyboardButton(text="🔄 Перезапустить", callback_data=f"restart:{target}"),
            ],
            [InlineKeyboardButton(text="⬅️ К выбору логов", callback_data="menu:logs")],
        ]
    )

    if callback.message:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.message(Command("reboot"))
async def cmd_reboot(message: Message) -> None:
    """Запрос подтверждения перезагрузки сервера."""
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚠️ Да, перезагрузить сервер", callback_data="reboot:confirm"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="reboot:cancel"),
            ]
        ]
    )
    await message.answer(
        "⚠️ <b>ВНИМАНИЕ: Перезагрузка сервера!</b>\n\n"
        "Вы действительно хотите отправить физический сервер в перезагрузку? "
        "Все сервисы будут временно недоступны на 1-2 минуты.",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "reboot:cancel")
async def callback_reboot_cancel(callback: CallbackQuery) -> None:
    """Отмена перезагрузки."""
    if callback.message:
        await callback.message.edit_text("❌ Перезагрузка отменена.")
    await callback.answer("Отменено.")


@router.callback_query(F.data == "reboot:confirm")
async def callback_reboot_confirm(callback: CallbackQuery) -> None:
    """Подтверждение перезагрузки сервера."""
    if callback.message:
        await callback.message.edit_text("⏳ <b>Инициируется перезагрузка сервера...</b>", parse_mode="HTML")
    await callback.answer("Перезагрузка запущена!")

    try:
        if os.name != "nt":
            subprocess.Popen(["sudo", "reboot"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["shutdown", "/r", "/t", "5"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        logger.error("Ошибка команды reboot: %s", e)
        if callback.message:
            await callback.message.edit_text(f"❌ Ошибка вызова reboot: {e}")


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

    middleware = AdminAccessMiddleware(settings.TELEGRAM_ADMIN_ID, docker_client)
    router.message.middleware(middleware)
    router.callback_query.middleware(middleware)

    dp.include_router(router)

    monitor_service = MonitorService(
        bot=bot,
        admin_id=settings.TELEGRAM_ADMIN_ID,
        docker_client=docker_client,
        check_interval=settings.TELEGRAM_CHECK_INTERVAL_SECONDS,
        alerts_enabled=settings.TELEGRAM_ALERTS_ENABLED,
    )

    return bot, dp, monitor_service
