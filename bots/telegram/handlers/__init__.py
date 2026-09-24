"""Агрегатор хендлеров Telegram-бота."""

from __future__ import annotations

from aiogram import Router

from bots.telegram.handlers.backup import router as backup_router
from bots.telegram.handlers.base import router as base_router
from bots.telegram.handlers.containers import router as containers_router
from bots.telegram.handlers.maintenance import router as maintenance_router
from bots.telegram.handlers.status import router as status_router
from bots.telegram.handlers.two_factor import router as two_factor_router


def get_handlers_router() -> Router:
    """Создать и скомпоновать единый роутер со всеми обработчиками."""
    main_router = Router(name="main_telegram_router")
    main_router.include_router(base_router)
    main_router.include_router(status_router)
    main_router.include_router(containers_router)
    main_router.include_router(maintenance_router)
    main_router.include_router(two_factor_router)
    main_router.include_router(backup_router)
    return main_router
