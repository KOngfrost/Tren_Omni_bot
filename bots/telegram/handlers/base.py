"""Базовые команды: /start, /help, /panel (Mini App)."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bots.telegram.keyboards import get_main_reply_keyboard, get_panel_inline_keyboard
from core.config import Settings

logger = logging.getLogger(__name__)
router = Router(name="base")


@router.message(CommandStart())
async def cmd_start(message: Message, settings: Settings) -> None:
    """Приветственное сообщение и инициализация панели управления."""
    webapp_url = settings.TELEGRAM_WEBAPP_URL
    text = (
        "👋 <b>Панель управления и мониторинга OSS Bot</b>\n\n"
        "Добро пожаловать в центр управления инфраструктурой студенческого бота.\n\n"
        "⚡ <b>Быстрый доступ:</b>\n"
        "• Нажмите <b>📱 Веб-панель</b> для открытия веб-интерфейса прямо в Telegram (Mini App).\n"
        "• Используйте кнопку <b>📊 Статус</b> или команду /status для проверки серверов.\n\n"
        "<i>Поддерживаются горячие кнопки быстрого меню и аккуратные команды через слэш (/).</i>"
    )
    reply_kb = get_main_reply_keyboard(webapp_url)
    inline_kb = get_panel_inline_keyboard(webapp_url)
    await message.answer(text, reply_markup=reply_kb, parse_mode="HTML")
    await message.answer("📱 <b>Telegram Mini App:</b>", reply_markup=inline_kb, parse_mode="HTML")


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: Message, settings: Settings) -> None:
    """Справка по доступным командам."""
    webapp_url = settings.TELEGRAM_WEBAPP_URL
    text = (
        "📖 <b>Справочник команд администратора:</b>\n\n"
        "📊 <b>/status</b> — Ресурсы сервера (CPU, RAM, Диск) и статус Docker-контейнеров\n"
        "📱 <b>/panel</b> — Открыть веб-панель управления (Telegram Mini App)\n"
        "🔄 <b>/restart</b> — Интерактивный перезапуск отдельных контейнеров или всего стека\n"
        "📋 <b>/logs</b> — Просмотр последних строк журнала любого контейнера\n"
        "💾 <b>/backup</b> — Резервная копия БД: дамп сохраняется на сервере (0600), в чат — отчёт с SHA-256\n"
        "🚧 <b>/maintenance</b> — Включение или отключение режима технических работ (HTTP 503)\n"
        "🔐 <b>/2fa</b> — Включение или отключение двухфакторной аутентификации\n"
        "⚠️ <b>/reboot</b> — Перезапуск всех рабочих сервисов проекта (с подтверждением)\n\n"
        "🔔 <b>Автоматический мониторинг:</b>\n"
        "При сбоях контейнеров (падение, статус <i>unhealthy</i>) или критической нехватке "
        "ресурсов вы немедленно получите тревожное уведомление."
    )
    inline_kb = get_panel_inline_keyboard(webapp_url)
    await message.answer(text, reply_markup=inline_kb, parse_mode="HTML")


@router.message(Command("panel"))
@router.message(Command("app"))
@router.message(Command("webapp"))
@router.message(F.text == "📱 Веб-панель")
async def cmd_panel(message: Message, settings: Settings) -> None:
    """Карточка запуска Telegram Mini App."""
    webapp_url = settings.TELEGRAM_WEBAPP_URL
    text = (
        "📱 <b>Веб-панель управления OSS Bot (Mini App)</b>\n\n"
        f"🔗 <b>Домен:</b> <code>{webapp_url}</code>\n"
        "🔒 <b>Протокол:</b> Защищённый HTTPS (Let's Encrypt / TLS)\n\n"
        "Нажмите кнопку ниже, чтобы открыть полнофункциональную веб-панель "
        "прямо внутри Telegram:"
    )
    inline_kb = get_panel_inline_keyboard(webapp_url)
    await message.answer(text, reply_markup=inline_kb, parse_mode="HTML")
