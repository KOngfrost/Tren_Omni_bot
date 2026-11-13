"""хендлеры управления списком администраторов telegram-бота."""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from core.config import Settings
from core.telegram_admins import (
    add_telegram_admin_id,
    get_admin_details,
    remove_telegram_admin_id,
)

logger = logging.getLogger(__name__)
router = Router(name="admins")


@router.message(Command("admins"))
@router.message(F.text == "Администраторы")
async def cmd_admins_list(message: Message, settings: Settings) -> None:
    """показать список всех авторизованных администраторов."""
    root_id = settings.TELEGRAM_ADMIN_ID
    data = await get_admin_details(root_id)
    admins = data.get("admins", [])

    lines = ["Список администраторов бота:"]
    for idx, adm in enumerate(admins, 1):
        role_label = adm["role"]
        aid = adm["id"]
        note_part = f" ({adm['note']})" if adm.get("note") else ""
        date_part = f" [добавлен: {adm['added_at'][:10]}]" if adm.get("added_at") and not adm["is_root"] else ""
        lines.append(f"{idx}. {role_label}: ID {aid}{note_part}{date_part}")

    lines.append(f"\nВсего администраторов: {len(admins)}")
    lines.append("\nКоманды главного администратора:")
    lines.append("• /addadmin <id> [примечание] — добавить администратора")
    lines.append("• /deladmin <id> — удалить администратора")

    await message.answer("\n".join(lines))


@router.message(Command("addadmin"))
async def cmd_addadmin(message: Message, settings: Settings) -> None:
    """добавить нового администратора (доступно только главному тех-админу)."""
    user_id = message.from_user.id if message.from_user else 0
    root_id = settings.TELEGRAM_ADMIN_ID

    if root_id <= 0 or user_id != root_id:
        await message.answer("Доступ запрещен. Управлять администраторами может только главный технический администратор.")
        return

    text = message.text or ""
    parts = text.strip().split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: /addadmin <telegram_id> [примечание]")
        return

    target_id = int(parts[1])
    note = parts[2] if len(parts) > 2 else ""

    ok, result_msg = await add_telegram_admin_id(
        admin_id=target_id,
        added_by=f"telegram:{user_id}",
        root_id=root_id,
        note=note,
    )

    if ok:
        await message.answer(f"Успешно: {html.escape(result_msg)}")
    else:
        await message.answer(f"Ошибка: {html.escape(result_msg)}")


@router.message(Command("deladmin"))
async def cmd_deladmin(message: Message, settings: Settings) -> None:
    """удалить администратора (доступно только главному тех-админу)."""
    user_id = message.from_user.id if message.from_user else 0
    root_id = settings.TELEGRAM_ADMIN_ID

    if root_id <= 0 or user_id != root_id:
        await message.answer("Доступ запрещен. Управлять администраторами может только главный технический администратор.")
        return

    text = message.text or ""
    parts = text.strip().split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: /deladmin <telegram_id>")
        return

    target_id = int(parts[1])

    ok, result_msg = await remove_telegram_admin_id(
        admin_id=target_id,
        root_id=root_id,
        removed_by=f"telegram:{user_id}",
    )

    if ok:
        await message.answer(f"Успешно: {html.escape(result_msg)}")
    else:
        await message.answer(f"Ошибка: {html.escape(result_msg)}")
