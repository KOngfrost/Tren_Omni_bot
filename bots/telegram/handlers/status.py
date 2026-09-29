"""Хендлеры статуса системы, бизнес-метрик и состояния Docker-контейнеров."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bots.telegram.app_metrics import collect_app_metrics, format_app_metrics_message
from bots.telegram.docker_client import DockerClient
from bots.telegram.keyboards import (
    STATUS_VIEW_APP,
    STATUS_VIEW_INFRA,
    get_metrics_inline_keyboard,
    get_status_inline_keyboard,
)
from bots.telegram.system_metrics import format_metrics_message, get_system_metrics
from core.maintenance import get_maintenance_info
from core.two_factor import is_two_factor_enabled

logger = logging.getLogger(__name__)
router = Router(name="status")


async def render_metrics_content(webapp_url: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
    """Сформировать текст и клавиатуру карточки бизнес-метрик сайта и бота."""
    metrics = await collect_app_metrics()
    return format_app_metrics_message(metrics), get_metrics_inline_keyboard(webapp_url)


async def render_status_content(
    docker_client: DockerClient,
    webapp_url: str | None = None,
    view: str = STATUS_VIEW_INFRA,
) -> tuple[str, InlineKeyboardMarkup]:
    """Сформировать текст и инлайн-клавиатуру карточки статуса.

    При view=STATUS_VIEW_INFRA показываются ресурсы сервера и контейнеры
    Docker (поведение по умолчанию). При view=STATUS_VIEW_APP карточка
    статуса заменяется бизнес-метриками сайта и бота — их тоже нужно видно
    из /status, а не только из отдельной команды /metrics.
    """
    if view == STATUS_VIEW_APP:
        text, _ = await render_metrics_content(webapp_url)
        # Клавиатура статуса: сохраняем переключатель вида и «Обновить».
        return text, get_status_inline_keyboard(webapp_url, view=view)

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

    keyboard = get_status_inline_keyboard(webapp_url)
    return "\n".join(lines), keyboard


@router.message(Command("status"))
@router.message(F.text == "📊 Статус")
async def cmd_status(
    message: Message,
    docker_client: DockerClient,
) -> None:
    """Вывод подробного статуса сервера и контейнеров."""
    text, keyboard = await render_status_content(docker_client)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "status:refresh")
async def callback_status_refresh(
    callback: CallbackQuery,
    docker_client: DockerClient,
) -> None:
    """Обновление карточки статуса сервера."""
    text, keyboard = await render_status_content(docker_client)
    try:
        if callback.message:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer("Данные обновлены.")
    except Exception:
        await callback.answer("Данные актуальны.")


@router.message(Command("metrics"))
@router.message(Command("stats"))
@router.message(F.text == "📈 Статистика")
async def cmd_metrics(message: Message) -> None:
    """Бизнес-метрики сайта и VK-бота: заявки, пользователи, очередь outbox."""
    text, keyboard = await render_metrics_content()
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "metrics:refresh")
async def callback_metrics_refresh(callback: CallbackQuery) -> None:
    """Немедленное обновление бизнес-метрик."""
    text, keyboard = await render_metrics_content()
    try:
        if callback.message:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer("Показатели обновлены.")
    except Exception:
        await callback.answer("Показатели актуальны.")


@router.callback_query(F.data.startswith("status:view:"))
async def callback_status_switch_view(
    callback: CallbackQuery,
    docker_client: DockerClient,
) -> None:
    """Переключение карточки между «Железо» и «Показатели сайта и бота»."""
    view = (callback.data or "").rsplit(":", 1)[-1]
    if view not in (STATUS_VIEW_INFRA, STATUS_VIEW_APP):
        await callback.answer("Неизвестный раздел.")
        return

    text, keyboard = await render_status_content(docker_client, view=view)
    try:
        if callback.message:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()
    except Exception:
        # Telegram отклоняет edit_text, если текст не изменился.
        await callback.answer("Данные актуальны.")


@router.callback_query(F.data == "status:noop")
async def callback_status_noop(callback: CallbackQuery) -> None:
    """Заглушка для уже активного раздела (кнопка не должна ничего делать)."""
    await callback.answer("Этот раздел уже открыт.")
