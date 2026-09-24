"""Telegram-бот для мониторинга сервера и управления контейнерами."""

from __future__ import annotations

import contextlib
import gzip
import html
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
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
from core.maintenance import get_maintenance_info, set_maintenance_mode
from core.two_factor import get_two_factor_info, is_two_factor_enabled, set_two_factor_mode

logger = logging.getLogger(__name__)


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


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Главная клавиатура команд."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статус"), KeyboardButton(text="🔄 Перезапуск")],
            [KeyboardButton(text="📋 Логи"), KeyboardButton(text="💾 Бэкап")],
            [KeyboardButton(text="🚧 Техработы"), KeyboardButton(text="🔐 2FA")],
            [KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
    )


async def render_status_content(docker_client: DockerClient) -> tuple[str, InlineKeyboardMarkup]:
    """Сформировать текст и инлайн-клавиатуру статуса сервера."""
    metrics = get_system_metrics()
    metrics_text = format_metrics_message(metrics)

    containers = await docker_client.list_containers(all=True)

    maint_info = await get_maintenance_info()
    maint_status = (
        "🔴 <b>ВКЛЮЧЕН</b>"
        if maint_info.get("enabled")
        else "🟢 <b>Выключен</b>"
    )

    two_factor_on = await is_two_factor_enabled()
    two_factor_status = (
        "🟢 <b>ВКЛЮЧЕНА</b>"
        if two_factor_on
        else "⚪ <b>Отключена</b>"
    )

    lines = [
        metrics_text,
        "",
        f"🚧 <b>Режим техработ:</b> {maint_status}",
        f"🔐 <b>2FA (два фактора):</b> {two_factor_status}",
        "",
        "📦 <b>Контейнеры Docker:</b>",
    ]
    if not containers:
        lines.append("<i>Контейнеры не найдены или Docker недоступен</i>")
    else:
        sorted_containers = sorted(containers, key=lambda x: (not x.is_project_container, x.name))
        for c in sorted_containers:
            status_clean = c.status
            health_part = ""
            if c.health and f"({c.health.lower()})" not in status_clean.lower():
                health_part = f" ({c.health})"

            if "migrate" in c.name and "Exited (0)" in status_clean:
                lines.append(f"⚪ <code>{c.name}</code> — Миграция БД (выполнена)")
            else:
                lines.append(f"{c.status_emoji} <code>{c.name}</code> — {status_clean}{health_part}")

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="status:refresh"),
                InlineKeyboardButton(text="🔄 Перезапуск", callback_data="menu:restart"),
            ],
            [
                InlineKeyboardButton(text="📋 Логи", callback_data="menu:logs"),
                InlineKeyboardButton(text="💾 Бэкап БД", callback_data="backup:create"),
            ],
            [
                InlineKeyboardButton(text="🚧 Техработы", callback_data="maint:menu"),
                InlineKeyboardButton(text="🔐 2FA", callback_data="2fa:menu"),
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
        "💾 <b>/backup</b> — Создать и выгрузить резервную копию базы данных\n"
        "🚧 <b>/maintenance</b> — Включение/отключение режима технических работ\n"
        "🔐 <b>/2fa</b> — Включение/отключение двухфакторной аутентификации\n"
        "⚠️ <b>/reboot</b> — Перезапуск контейнеров проекта (с подтверждением)\n\n"
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


async def render_maintenance_content() -> tuple[str, InlineKeyboardMarkup]:
    """Сформировать текст и инлайн-клавиатуру управления режимом техработ."""
    info = await get_maintenance_info()
    enabled = bool(info.get("enabled", False))
    updated_at = info.get("updated_at", "")
    updated_by = info.get("updated_by", "")

    if enabled:
        status_line = "🔴 <b>ВКЛЮЧЕН</b> (Технические работы активны)"
        desc_line = (
            "⚠️ <b>Внимание:</b>\n"
            "• Веб-панель возвращает страницу обслуживания (HTTP 503).\n"
            "• VK-бот отправляет студентам автоответ о техработах.\n"
            "• Доступ в веб-панель открыт только для суперадминистраторов.\n\n"
            f"🕒 <i>Включен: {html.escape(updated_at)}</i>\n"
            f"👤 <i>Инициатор: {html.escape(updated_by)}</i>"
        )
        buttons = [
            [InlineKeyboardButton(text="🟢 Отключить техработы", callback_data="maint:disable")],
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="maint:refresh"),
                InlineKeyboardButton(text="📊 Статус", callback_data="status:refresh"),
            ],
        ]
    else:
        status_line = "🟢 <b>ВЫКЛЮЧЕН</b> (Штатный режим)"
        desc_line = (
            "Все системы (веб-панель и боты) работают в обычном режиме.\n\n"
            "При включении режима:\n"
            "• Для пользователей веб-панель закроется заглушкой (HTTP 503)\n"
            "• Бот ВК перестанет принимать обращения и предупредит о работах"
        )
        buttons = [
            [InlineKeyboardButton(text="🔴 Включить техработы", callback_data="maint:enable")],
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="maint:refresh"),
                InlineKeyboardButton(text="📊 Статус", callback_data="status:refresh"),
            ],
        ]

    text = (
        f"🚧 <b>Управление режимом технических работ</b>\n\n"
        f"Текущее состояние: {status_line}\n\n"
        f"{desc_line}"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("maintenance"))
@router.message(Command("maint"))
@router.message(F.text == "🚧 Техработы")
@router.callback_query(F.data == "maint:menu")
async def cmd_maintenance(event: Message | CallbackQuery) -> None:
    """Управление режимом технических работ."""
    text, keyboard = await render_maintenance_content()
    if isinstance(event, CallbackQuery):
        if event.message:
            await event.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "maint:refresh")
async def callback_maintenance_refresh(callback: CallbackQuery) -> None:
    """Обновление карточки техработ."""
    text, keyboard = await render_maintenance_content()
    try:
        if callback.message:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer("Статус техработ обновлён.")
    except Exception:
        await callback.answer("Данные актуальны.")


@router.callback_query(F.data == "maint:enable")
async def callback_maintenance_enable(callback: CallbackQuery) -> None:
    """Включение режима техработ."""
    user_id = callback.from_user.id if callback.from_user else 0
    await set_maintenance_mode(
        enabled=True,
        updated_by=f"telegram:{user_id}",
    )
    await callback.answer("🚨 Режим техработ ВКЛЮЧЕН!", show_alert=True)
    text, keyboard = await render_maintenance_content()
    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "maint:disable")
async def callback_maintenance_disable(callback: CallbackQuery) -> None:
    """Выключение режима техработ."""
    user_id = callback.from_user.id if callback.from_user else 0
    await set_maintenance_mode(
        enabled=False,
        updated_by=f"telegram:{user_id}",
    )
    await callback.answer("✅ Режим техработ ВЫКЛЮЧЕН! Системы работают штатно.", show_alert=True)
    text, keyboard = await render_maintenance_content()
    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")


async def render_two_factor_content() -> tuple[str, InlineKeyboardMarkup]:
    """Сформировать текст и инлайн-клавиатуру управления 2FA."""
    info = await get_two_factor_info()
    enabled = info.get("enabled", True)
    updated_at = info.get("updated_at", "")
    updated_by = info.get("updated_by", "")

    if enabled:
        status_line = "🟢 <b>ВКЛЮЧЕНА</b> (Код подтверждения в VK)"
        desc_line = (
            "Для входа в веб-панель требуется ввести 6-значный одноразовый код, "
            "который отправляется администратору в личные сообщения ВКонтакте.\n\n"
            "При отключении 2FA авторизация в панель управления происходит "
            "напрямую по логину и паролю без запроса OTP-кода."
        )
        buttons = [
            [InlineKeyboardButton(text="⚪ Отключить 2FA", callback_data="2fa:disable")],
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="2fa:refresh"),
                InlineKeyboardButton(text="📊 Статус", callback_data="status:refresh"),
            ],
        ]
    else:
        status_line = "⚪ <b>ОТКЛЮЧЕНА</b> (Вход по логину и паролю)"
        desc_line = (
            "Двухфакторная аутентификация выключена. Авторизация в веб-панель "
            "осуществляется по логину и паролю без подтверждения через VK.\n\n"
            "Рекомендуется включать 2FA в рабочей среде для защиты административного доступа."
        )
        buttons = [
            [InlineKeyboardButton(text="🟢 Включить 2FA", callback_data="2fa:enable")],
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="2fa:refresh"),
                InlineKeyboardButton(text="📊 Статус", callback_data="status:refresh"),
            ],
        ]

    audit_part = ""
    if updated_at:
        audit_part = f"\n\n<i>Последнее изменение: {updated_at[:19]} ({updated_by})</i>"

    text = (
        f"🔐 <b>Управление двухфакторной аутентификацией (2FA)</b>\n\n"
        f"Текущее состояние: {status_line}\n\n"
        f"{desc_line}{audit_part}"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("2fa"))
@router.message(F.text == "🔐 2FA")
@router.callback_query(F.data == "2fa:menu")
async def cmd_two_factor(event: Message | CallbackQuery) -> None:
    """Управление двухфакторной аутентификацией (2FA)."""
    text, keyboard = await render_two_factor_content()
    if isinstance(event, CallbackQuery):
        if event.message:
            await event.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "2fa:refresh")
async def callback_two_factor_refresh(callback: CallbackQuery) -> None:
    """Обновление статуса 2FA."""
    text, keyboard = await render_two_factor_content()
    try:
        if callback.message:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer("Статус 2FA обновлён.")
    except Exception:
        await callback.answer("Данные актуальны.")


@router.callback_query(F.data == "2fa:enable")
async def callback_two_factor_enable(callback: CallbackQuery) -> None:
    """Включение 2FA."""
    user_id = callback.from_user.id if callback.from_user else 0
    await set_two_factor_mode(
        enabled=True,
        updated_by=f"telegram:{user_id}",
    )
    await callback.answer("🔒 Двухфакторная аутентификация (2FA) ВКЛЮЧЕНА!", show_alert=True)
    text, keyboard = await render_two_factor_content()
    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "2fa:disable")
async def callback_two_factor_disable(callback: CallbackQuery) -> None:
    """Отключение 2FA."""
    user_id = callback.from_user.id if callback.from_user else 0
    await set_two_factor_mode(
        enabled=False,
        updated_by=f"telegram:{user_id}",
    )
    await callback.answer("🔓 Двухфакторная аутентификация (2FA) ОТКЛЮЧЕНА! Вход доступен по логину и паролю.", show_alert=True)
    text, keyboard = await render_two_factor_content()
    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")


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
    """Запрос подтверждения перезапуска сервисов проекта."""
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Да, перезапустить сервисы", callback_data="reboot:confirm"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="reboot:cancel"),
            ]
        ]
    )
    await message.answer(
        "⚠️ <b>ВНИМАНИЕ: Перезапуск сервисов OSS Bot!</b>\n\n"
        "Вы действительно хотите перезапустить все рабочие контейнеры проекта?\n"
        "Сервисы будут кратковременно недоступны (10-30 секунд).\n\n"
        "<i>Для полной перезагрузки физического сервера используйте SSH: <code>sudo reboot</code></i>",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "reboot:cancel")
async def callback_reboot_cancel(callback: CallbackQuery) -> None:
    """Отмена перезапуска."""
    if callback.message:
        await callback.message.edit_text("❌ Перезапуск отменен.")
    await callback.answer("Отменено.")


@router.callback_query(F.data == "reboot:confirm")
async def callback_reboot_confirm(callback: CallbackQuery, docker_client: DockerClient) -> None:
    """Подтверждение перезапуска: перезапуск контейнеров проекта."""
    if callback.message:
        await callback.message.edit_text("⏳ <b>Инициируется перезапуск сервисов OSS Bot...</b>", parse_mode="HTML")
    await callback.answer("Перезапуск запущен!")

    try:
        restarted: list[str] = []
        failed: list[str] = []
        containers = await docker_client.list_containers(all=False)
        for c in containers:
            if c.is_project_container and "monitor" not in c.name:
                ok = await docker_client.restart_container(c.id)
                if ok:
                    restarted.append(c.name)
                else:
                    failed.append(c.name)

        res = "✅ <b>Сервисы успешно перезапущены:</b>\n" + "\n".join(f"• <code>{n}</code>" for n in restarted)
        if failed:
            res += "\n\n❌ <b>Не удалось перезапустить:</b>\n" + "\n".join(f"• <code>{n}</code>" for n in failed)
        res += "\n\n💡 <i>Примечание: перезагрузка физического сервера выполняется через SSH-терминал (`sudo reboot`).</i>"
        if callback.message:
            await callback.message.edit_text(res, parse_mode="HTML")
    except Exception as e:
        logger.error("Ошибка при перезапуске сервисов: %s", e)
        if callback.message:
            await callback.message.edit_text(f"❌ Ошибка перезапуска: {e}")


def rotate_old_backups(backup_dir: str | None = None, max_days: int = 7) -> int:
    """Удалить локальные файлы резервных копий старше max_days дней.

    Возвращает количество удаленных файлов.
    """
    target_dir = backup_dir or os.getenv("BACKUP_DIR", "/var/backups/oss_bot")
    if not os.path.exists(target_dir):
        return 0
    deleted_count = 0
    cutoff_time = time.time() - (max_days * 86400)
    try:
        for entry in os.scandir(target_dir):
            if entry.is_file() and entry.name.startswith("oss_bot_backup_"):
                try:
                    if entry.stat().st_mtime < cutoff_time:
                        os.remove(entry.path)
                        deleted_count += 1
                        logger.info("Удалена устаревшая резервная копия: %s", entry.name)
                except Exception as exc:
                    logger.warning("Не удалось удалить устаревший файл бэкапа %s: %s", entry.name, exc)
    except Exception as e:
        logger.warning("Ошибка сканирования каталога бэкапов %s: %s", target_dir, e)
    return deleted_count


def _save_local_copy(data: bytes, filename: str) -> None:
    """Сохранить резервную копию на диск и выполнить ротацию устаревших копий."""
    backup_dir = os.getenv("BACKUP_DIR", "/var/backups/oss_bot")
    try:
        os.makedirs(backup_dir, exist_ok=True)
        with open(os.path.join(backup_dir, filename), "wb") as f:
            f.write(data)
        rotate_old_backups(backup_dir, max_days=7)
    except Exception as e:
        logger.debug("Не удалось сохранить локальную копию бэкапа: %s", e)


async def perform_database_backup(
    docker_client: DockerClient,
    settings: Settings,
) -> tuple[bool, bytes | None, str]:
    """Выполнить резервное копирование базы данных.

    Возвращает (success, compressed_dump_bytes, filename_or_error).
    """
    now = datetime.now(UTC)
    stamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"oss_bot_backup_{stamp}.sql.gz"

    user = settings.DB_USER or os.getenv("POSTGRES_USER", "oss_bot")
    db = settings.DB_NAME or os.getenv("POSTGRES_DB", "oss_bot")
    password = settings.DB_PASS or os.getenv("POSTGRES_PASSWORD", "")

    # 1. Попытка создания бэкапа через Docker (production-стек)
    containers = await docker_client.list_containers(all=False)
    db_container = None
    for c in containers:
        name_lower = c.name.lower()
        if ("db" in name_lower or "postgres" in name_lower) and "pgbouncer" not in name_lower:
            db_container = c.name
            break

    if db_container:
        cmd = ["pg_dump", "-U", user, "-d", db, "--no-owner", "--clean", "--if-exists"]
        env = [f"PGPASSWORD={password}"] if password else None
        exit_code, stdout, stderr = await docker_client.exec_command(db_container, cmd, env=env)
        if exit_code == 0 and stdout:
            compressed = gzip.compress(stdout)
            _save_local_copy(compressed, filename)
            return True, compressed, filename
        err_msg = stderr.decode("utf-8", errors="replace").strip() or f"pg_dump exit code {exit_code}"
        return False, None, f"Ошибка в контейнере {db_container}: {err_msg}"

    # 2. Локальный/тестовый фоллбек: если SQLite
    for sqlite_file in ("test_db.sqlite3", "dev.db", "student_bot.db"):
        if os.path.exists(sqlite_file):
            with open(sqlite_file, "rb") as f:
                compressed = gzip.compress(f.read())
            sqlite_filename = f"oss_bot_backup_{stamp}.sqlite.gz"
            _save_local_copy(compressed, sqlite_filename)
            return True, compressed, sqlite_filename

    return False, None, "Контейнер базы данных не найден или Docker недоступен."


@router.message(Command("backup"))
@router.message(F.text == "💾 Бэкап")
@router.callback_query(F.data == "backup:create")
async def cmd_backup(
    event: Message | CallbackQuery,
    docker_client: DockerClient,
    settings: Settings,
) -> None:
    """Создание и выгрузка резервной копии базы данных."""
    is_callback = isinstance(event, CallbackQuery)
    target_message = (
        event.message if is_callback and event.message else (event if isinstance(event, Message) else None)
    )

    if is_callback:
        await event.answer("Запуск создания бэкапа...")

    status_msg = None
    if target_message:
        status_msg = await target_message.answer(
            "⏳ <b>Создание резервной копии базы данных...</b>\nПожалуйста, подождите, идёт выгрузка данных.",
            parse_mode="HTML",
        )

    ok, data, info = await perform_database_backup(docker_client, settings)

    if ok and data:
        size_kb = len(data) / 1024
        size_str = f"{size_kb / 1024:.2f} МБ" if size_kb > 1024 else f"{size_kb:.1f} КБ"
        doc = BufferedInputFile(file=data, filename=info)
        caption = (
            "✅ <b>Резервная копия базы данных успешно создана!</b>\n\n"
            f"📦 <b>Размер архива:</b> {size_str}\n"
            f"💾 <b>База данных:</b> <code>{settings.DB_NAME}</code>\n"
            f"🕒 <b>Дата создания:</b> <code>{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC</code>\n"
            f"📁 <b>Имя файла:</b> <code>{info}</code>"
        )
        if target_message:
            if len(data) > 50 * 1024 * 1024:
                caption += (
                    "\n\n⚠️ <i>Размер архива превышает 50 МБ (лимит Telegram Bot API). "
                    "Файл сохранён локально на сервере в каталоге бэкапов.</i>"
                )
                await target_message.answer(caption, parse_mode="HTML")
            else:
                try:
                    await target_message.answer_document(document=doc, caption=caption, parse_mode="HTML")
                except Exception as exc:
                    logger.warning("Не удалось отправить файл бэкапа в Telegram: %s", exc)
                    caption += f"\n\n⚠️ <i>Не удалось отправить документ в Telegram: {exc}. Архив сохранён на сервере.</i>"
                    await target_message.answer(caption, parse_mode="HTML")
        if status_msg:
            with contextlib.suppress(Exception):
                await status_msg.delete()
    else:
        err_text = html.escape(info)
        fail_text = f"❌ <b>Не удалось создать резервную копию:</b>\n\n<code>{err_text}</code>"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="backup:create")],
                [InlineKeyboardButton(text="📊 К статусу", callback_data="status:refresh")],
            ]
        )
        if status_msg:
            await status_msg.edit_text(fail_text, reply_markup=kb, parse_mode="HTML")
        elif target_message:
            await target_message.answer(fail_text, reply_markup=kb, parse_mode="HTML")


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
        settings=settings,
    )

    return bot, dp, monitor_service
