"""Хендлеры управления двухфакторной аутентификацией (2FA)."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bots.telegram.keyboards import get_two_factor_inline_keyboard
from core.two_factor import get_two_factor_info, set_two_factor_mode

logger = logging.getLogger(__name__)
router = Router(name="two_factor")


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
    else:
        status_line = "⚪ <b>ОТКЛЮЧЕНА</b> (Вход по логину и паролю)"
        desc_line = (
            "Двухфакторная аутентификация выключена. Авторизация в веб-панель "
            "осуществляется по логину и паролю без подтверждения через VK.\n\n"
            "Рекомендуется включать 2FA в рабочей среде для защиты административного доступа."
        )

    audit_part = ""
    if updated_at:
        audit_part = f"\n\n<i>Последнее изменение: {updated_at[:19]} ({updated_by})</i>"

    text = (
        f"🔐 <b>Управление двухфакторной аутентификацией (2FA)</b>\n\n"
        f"Текущее состояние: {status_line}\n\n"
        f"{desc_line}{audit_part}"
    )
    keyboard = get_two_factor_inline_keyboard(enabled)
    return text, keyboard


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
