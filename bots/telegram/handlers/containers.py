"""Хендлеры управления контейнерами: перезапуск, логи, перезагрузка стека."""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bots.telegram.docker_client import DockerClient
from bots.telegram.keyboards import get_reboot_confirmation_keyboard

logger = logging.getLogger(__name__)
router = Router(name="containers")


@router.message(Command("restart"))
@router.message(F.text == "🔄 Перезапуск")
@router.callback_query(F.data == "menu:restart")
async def cmd_restart_menu(
    event: Message | CallbackQuery,
    docker_client: DockerClient,
) -> None:
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
async def callback_restart_all(
    callback: CallbackQuery,
    docker_client: DockerClient,
) -> None:
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
async def callback_restart_single(
    callback: CallbackQuery,
    docker_client: DockerClient,
) -> None:
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
async def cmd_logs_menu(
    event: Message | CallbackQuery,
    docker_client: DockerClient,
) -> None:
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
async def callback_view_logs(
    callback: CallbackQuery,
    docker_client: DockerClient,
) -> None:
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
    kb = get_reboot_confirmation_keyboard()
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
async def callback_reboot_confirm(
    callback: CallbackQuery,
    docker_client: DockerClient,
) -> None:
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
