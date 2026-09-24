"""Клавиатуры для Telegram-бота мониторинга (Reply и Inline, включая Mini App)."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from core.config import get_settings


def get_main_reply_keyboard(webapp_url: str | None = None) -> ReplyKeyboardMarkup:
    """Главная клавиатура команд с поддержкой кнопки Telegram Mini App."""
    url = webapp_url or get_settings().TELEGRAM_WEBAPP_URL
    rows: list[list[KeyboardButton]] = []

    if url and url.startswith("https://"):
        rows.append([KeyboardButton(text="📱 Веб-панель", web_app=WebAppInfo(url=url))])

    rows.extend([
        [KeyboardButton(text="📊 Статус"), KeyboardButton(text="🔄 Перезапуск")],
        [KeyboardButton(text="📋 Логи"), KeyboardButton(text="💾 Бэкап")],
        [KeyboardButton(text="🚧 Техработы"), KeyboardButton(text="🔐 2FA")],
        [KeyboardButton(text="ℹ️ Помощь")],
    ])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
    )


def get_status_inline_keyboard(webapp_url: str | None = None) -> InlineKeyboardMarkup:
    """Инлайн-клавиатура для карточки статуса сервера."""
    url = webapp_url or get_settings().TELEGRAM_WEBAPP_URL
    buttons: list[list[InlineKeyboardButton]] = []

    if url and url.startswith("https://"):
        buttons.append([
            InlineKeyboardButton(text="📱 Открыть веб-панель", web_app=WebAppInfo(url=url)),
        ])

    buttons.extend([
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
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_panel_inline_keyboard(webapp_url: str | None = None) -> InlineKeyboardMarkup:
    """Инлайн-клавиатура для запуска Mini App."""
    url = webapp_url or get_settings().TELEGRAM_WEBAPP_URL
    buttons: list[list[InlineKeyboardButton]] = []

    if url and url.startswith("https://"):
        buttons.append([
            InlineKeyboardButton(text="🚀 Запустить веб-панель", web_app=WebAppInfo(url=url)),
        ])
        buttons.append([
            InlineKeyboardButton(text="🌐 Открыть в браузере", url=url),
        ])

    buttons.append([
        InlineKeyboardButton(text="📊 Статус сервера", callback_data="status:refresh"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_maintenance_inline_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    """Инлайн-клавиатура управления режимом техработ."""
    if enabled:
        action_btn = InlineKeyboardButton(text="🟢 Отключить техработы", callback_data="maint:disable")
    else:
        action_btn = InlineKeyboardButton(text="🔴 Включить техработы", callback_data="maint:enable")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [action_btn],
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="maint:refresh"),
                InlineKeyboardButton(text="📊 Статус", callback_data="status:refresh"),
            ],
        ]
    )


def get_two_factor_inline_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    """Инлайн-клавиатура управления 2FA."""
    if enabled:
        action_btn = InlineKeyboardButton(text="⚪ Отключить 2FA", callback_data="2fa:disable")
    else:
        action_btn = InlineKeyboardButton(text="🟢 Включить 2FA", callback_data="2fa:enable")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [action_btn],
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="2fa:refresh"),
                InlineKeyboardButton(text="📊 Статус", callback_data="status:refresh"),
            ],
        ]
    )


def get_reboot_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения перезапуска всех сервисов."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Да, перезапустить сервисы", callback_data="reboot:confirm"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="reboot:cancel"),
            ]
        ]
    )
