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

# Срезы карточки статуса: инфраструктура или бизнес-показатели.
STATUS_VIEW_INFRA = "infra"
STATUS_VIEW_APP = "app"


def get_main_reply_keyboard(webapp_url: str | None = None) -> ReplyKeyboardMarkup:
    """Главная клавиатура команд с поддержкой кнопки Telegram Mini App."""
    url = webapp_url or get_settings().TELEGRAM_WEBAPP_URL
    rows: list[list[KeyboardButton]] = []

    if url and url.startswith("https://"):
        rows.append([KeyboardButton(text="📱 Веб-панель", web_app=WebAppInfo(url=url))])

    rows.extend(
        [
            [KeyboardButton(text="📊 Статус"), KeyboardButton(text="📈 Статистика")],
            [KeyboardButton(text="🔄 Перезапуск"), KeyboardButton(text="📋 Логи")],
            [KeyboardButton(text="💾 Бэкап"), KeyboardButton(text="🚧 Техработы")],
            [KeyboardButton(text="🔐 2FA"), KeyboardButton(text="🚪 Сброс сессий")],
            [KeyboardButton(text="ℹ️ Помощь")],
        ]
    )

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
    )


def get_status_inline_keyboard(
    webapp_url: str | None = None,
    view: str = STATUS_VIEW_INFRA,
) -> InlineKeyboardMarkup:
    """Инлайн-клавиатура для карточки статуса.

    `view` переключает карточку между двумя срезами:
    - STATUS_VIEW_INFRA («🖥 Железо и контейнеры») — CPU/RAM/Диск/Docker;
    - STATUS_VIEW_APP («📊 Показатели сайта и бота») — бизнес-метрики.
    """
    url = webapp_url or get_settings().TELEGRAM_WEBAPP_URL
    buttons: list[list[InlineKeyboardButton]] = []

    if url and url.startswith("https://"):
        buttons.append(
            [
                InlineKeyboardButton(text="📱 Открыть веб-панель", web_app=WebAppInfo(url=url)),
            ]
        )

    # Переключатель вида карточки: активный срез помечен галочкой.
    if view == STATUS_VIEW_APP:
        infra_btn = InlineKeyboardButton(
            text="🖥 Железо и контейнеры", callback_data="status:view:infra"
        )
        app_btn = InlineKeyboardButton(
            text="✅ Показатели сайта и бота", callback_data="status:noop"
        )
    else:
        infra_btn = InlineKeyboardButton(
            text="✅ Железо и контейнеры", callback_data="status:noop"
        )
        app_btn = InlineKeyboardButton(
            text="📊 Показатели сайта и бота", callback_data="status:view:app"
        )
    buttons.append([infra_btn, app_btn])

    buttons.extend(
        [
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

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_panel_inline_keyboard(webapp_url: str | None = None) -> InlineKeyboardMarkup:
    """Инлайн-клавиатура для запуска Mini App."""
    url = webapp_url or get_settings().TELEGRAM_WEBAPP_URL
    buttons: list[list[InlineKeyboardButton]] = []

    if url and url.startswith("https://"):
        buttons.append(
            [
                InlineKeyboardButton(text="🚀 Запустить веб-панель", web_app=WebAppInfo(url=url)),
            ]
        )
        buttons.append(
            [
                InlineKeyboardButton(text="🌐 Открыть в браузере", url=url),
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(text="📊 Статус сервера", callback_data="status:refresh"),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_maintenance_inline_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    """Инлайн-клавиатура управления режимом техработ."""
    if enabled:
        action_btn = InlineKeyboardButton(
            text="🟢 Отключить техработы", callback_data="maint:disable"
        )
    else:
        action_btn = InlineKeyboardButton(
            text="🔴 Включить техработы", callback_data="maint:enable"
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [action_btn],
            [
                InlineKeyboardButton(text="🚪 Сбросить все сессии", callback_data="kickall:menu"),
            ],
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


def get_metrics_inline_keyboard(
    webapp_url: str | None = None,
    tab: str = "summary",
) -> InlineKeyboardMarkup:
    """Инлайн-клавиатура карточки бизнес-метрик с вкладками (Сводка/Сайт/Бот/KPI)."""
    url = webapp_url or get_settings().TELEGRAM_WEBAPP_URL
    buttons: list[list[InlineKeyboardButton]] = []

    if url and url.startswith("https://"):
        buttons.append(
            [
                InlineKeyboardButton(text="📱 Открыть веб-панель", web_app=WebAppInfo(url=url)),
            ]
        )

    # Вкладки метрик
    tabs = [
        ("summary", "📊 Сводка"),
        ("site", "🌐 Сайт"),
        ("bot", "🤖 Бот"),
        ("kpi", "🎯 KPI"),
    ]
    tab_row1: list[InlineKeyboardButton] = []
    tab_row2: list[InlineKeyboardButton] = []
    for idx, (code, label) in enumerate(tabs):
        text = f"• {label} •" if code == tab else label
        cb = "status:noop" if code == tab else f"metrics:tab:{code}"
        if idx < 2:
            tab_row1.append(InlineKeyboardButton(text=text, callback_data=cb))
        else:
            tab_row2.append(InlineKeyboardButton(text=text, callback_data=cb))
    buttons.append(tab_row1)
    buttons.append(tab_row2)

    buttons.extend(
        [
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="metrics:refresh"),
                InlineKeyboardButton(
                    text="🖥 Железо и контейнеры", callback_data="status:view:infra"
                ),
            ],
            [
                InlineKeyboardButton(text="📊 Статус сервера", callback_data="status:view:infra"),
                InlineKeyboardButton(text="📋 Логи", callback_data="menu:logs"),
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_kickall_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения сброса всех активных сессий."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚪 Да, завершить все сессии", callback_data="kickall:confirm"
                ),
                InlineKeyboardButton(text="❌ Отмена", callback_data="kickall:cancel"),
            ]
        ]
    )


def get_reboot_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения перезапуска всех сервисов."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Да, перезапустить сервисы", callback_data="reboot:confirm"
                ),
                InlineKeyboardButton(text="❌ Отмена", callback_data="reboot:cancel"),
            ]
        ]
    )
