"""Хендлеры управления режимом технических работ (Maintenance Mode)."""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bots.telegram.keyboards import get_maintenance_inline_keyboard
from core.maintenance import get_maintenance_info, set_maintenance_mode

logger = logging.getLogger(__name__)
router = Router(name="maintenance")


async def render_maintenance_content() -> tuple[str, InlineKeyboardMarkup]:
    """Сформировать текст и клавиатуру управления техработами."""
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
    else:
        status_line = "🟢 <b>ВЫКЛЮЧЕН</b> (Штатный режим)"
        desc_line = (
            "Все системы (веб-панель и боты) работают в обычном режиме.\n\n"
            "При включении режима:\n"
            "• Для пользователей веб-панель закроется заглушкой (HTTP 503)\n"
            "• Бот ВК перестанет принимать обращения и предупредит о работах"
        )

    text = (
        f"🚧 <b>Управление режимом технических работ</b>\n\n"
        f"Текущее состояние: {status_line}\n\n"
        f"{desc_line}"
    )
    keyboard = get_maintenance_inline_keyboard(enabled)
    return text, keyboard


@router.message(Command("maintenance"))
@router.message(Command("maint"))
@router.message(F.text == "🚧 Техработы")
@router.callback_query(F.data == "maint:menu")
async def cmd_maintenance(event: Message | CallbackQuery) -> None:
    """Отображение карточки режима техработ."""
    text, keyboard = await render_maintenance_content()
    if isinstance(event, CallbackQuery):
        if event.message:
            await event.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "maint:refresh")
async def callback_maintenance_refresh(callback: CallbackQuery) -> None:
    """Обновление статуса техработ."""
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
