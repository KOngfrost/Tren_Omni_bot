"""Хендлеры Технического администратора для VK-бота.

Предоставляет доступ к управлению сервером, контейнерами, бэкапами и
системными метриками. Доступ строго ограничен TECH_ADMIN_VK_ID из .env.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from vkbottle.bot import BotLabeler, Message

from bots.telegram.docker_client import DockerClient
from bots.telegram.system_metrics import get_system_metrics, render_progress_bar
from bots.vk.common import _main_keyboard_for
from bots.vk.keyboards import (
    build_tech_admin_keyboard,
    build_tech_containers_keyboard,
    build_tech_logs_keyboard,
    build_tech_maintenance_keyboard,
)
from core.backup_service import perform_isolated_database_backup
from core.bot_core import BotCore
from core.config import settings
from core.database import async_session_maker
from core.maintenance import is_maintenance_mode, set_maintenance_mode
from core.models import Ticket, TicketStatus, VkOutbox
from core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

tech_admin_labeler = BotLabeler()

COMMANDS_TECH_PANEL = [
    "🔧 тех-админ",
    "🔧 тех админ",
    "тех-админ",
    "тех админ",
    "тех-меню",
    "тех меню",
    "🔧 тех-меню",
    "/tech",
    "/server",
    "тех",
    "tech",
]

# Регулярные выражения для команд действий
RESTART_CONTAINER_PATTERN = re.compile(r"^🔄\s*перезапуск\s+([a-zA-Z0-9_\-]+)$", re.IGNORECASE)
LOGS_CONTAINER_PATTERN = re.compile(r"^📋\s*логи\s+([a-zA-Z0-9_\-]+)$", re.IGNORECASE)


def is_tech_admin(vk_id: int | None) -> bool:
    """Проверка прав: доступ разрешён исключительно TECH_ADMIN_VK_ID."""
    return BotCore.is_tech_admin_vk_id(vk_id)


async def _check_access(message: Message) -> bool:
    """Проверить доступ и отправить отказ, если пользователь не тех-админ."""
    if not is_tech_admin(message.from_id):
        logger.warning(
            "Отклонён неавторизованный запрос к тех-панели VK от vk_id=%s", message.from_id
        )
        await message.answer(
            "⛔ Доступ запрещён. Меню доступно исключительно Техническому администратору сервера.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return False
    return True


@tech_admin_labeler.private_message(text=COMMANDS_TECH_PANEL)
async def tech_panel_handler(message: Message) -> None:
    """Главный вход в меню технического администратора."""
    if not await _check_access(message):
        return

    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    text = (
        "🔧 <b>Панель Технического Администратора</b>\n\n"
        f"🕒 Текущее время: <code>{now_str}</code>\n"
        f"🏷 Версия: <code>v{settings.APP_VERSION}</code>\n"
        f"🛡 Доступ подтверждён для VK ID: <code>{message.from_id}</code>\n\n"
        "Выберите раздел для мониторинга или управления сервисами:"
    )
    await message.answer(text, keyboard=build_tech_admin_keyboard())


@tech_admin_labeler.private_message(text=["📊 Сервер", "сервер", "/tech_status"])
async def tech_status_handler(message: Message) -> None:
    """Состояние хоста: CPU, RAM, диск, аптайм и статус контейнеров."""
    if not await _check_access(message):
        return

    metrics = get_system_metrics()
    cpu_pct = metrics.get("cpu_percent", 0.0)
    ram_pct = metrics.get("ram_percent", 0.0)
    ram_used = metrics.get("ram_used", "0 MB")
    ram_total = metrics.get("ram_total", "0 MB")
    disk_pct = metrics.get("disk_percent", 0.0)
    disk_used = metrics.get("disk_used", "0 GB")
    disk_total = metrics.get("disk_total", "0 GB")
    uptime = metrics.get("uptime_formatted", "Н/Д")

    docker_client = DockerClient()
    container_lines = []
    try:
        containers = await docker_client.list_containers(all=True)
        if containers:
            for c in containers:
                icon = "🟢" if "up" in c.status.lower() else "🔴"
                container_lines.append(f"{icon} <code>{c.name}</code> ({c.status})")
        else:
            container_lines.append("⚠️ Контейнеры не обнаружены или сокет недоступен")
    except Exception as exc:
        container_lines.append(f"⚠️ Ошибка опроса Docker: {exc}")

    containers_block = "\n".join(container_lines) if container_lines else "Н/Д"

    text = (
        "📊 <b>Состояние сервера и инфраструктуры:</b>\n\n"
        f"⏱ <b>Uptime:</b> <code>{uptime}</code>\n\n"
        f"🖥 <b>CPU:</b> {render_progress_bar(cpu_pct)} <code>{cpu_pct:.1f}%</code>\n"
        f"🧠 <b>RAM:</b> {render_progress_bar(ram_pct)} <code>{ram_used} / {ram_total} ({ram_pct:.1f}%)</code>\n"
        f"💾 <b>Диск:</b> {render_progress_bar(disk_pct)} <code>{disk_used} / {disk_total} ({disk_pct:.1f}%)</code>\n\n"
        f"📦 <b>Контейнеры:</b>\n{containers_block}"
    )
    await message.answer(text, keyboard=build_tech_admin_keyboard())


@tech_admin_labeler.private_message(text=["📈 Метрики", "метрики", "/tech_metrics"])
async def tech_metrics_handler(message: Message) -> None:
    """Прикладные бизнес-метрики: тикеты, DB ping, Redis, очереди Outbox."""
    if not await _check_access(message):
        return

    # 1. Запрос метрик тикетов и outbox
    total_tickets = 0
    new_tickets = 0
    in_progress = 0
    completed = 0
    outbox_pending = 0
    outbox_failed = 0
    db_status = "🟢 OK"

    try:
        async with async_session_maker() as session:
            from sqlalchemy import func, select

            total_tickets = await session.scalar(select(func.count(Ticket.id))) or 0
            new_tickets = (
                await session.scalar(
                    select(func.count(Ticket.id)).where(Ticket.status == TicketStatus.NEW)
                )
                or 0
            )
            in_progress = (
                await session.scalar(
                    select(func.count(Ticket.id)).where(Ticket.status == TicketStatus.IN_PROGRESS)
                )
                or 0
            )
            completed = (
                await session.scalar(
                    select(func.count(Ticket.id)).where(
                        Ticket.status.in_((TicketStatus.COMPLETED, TicketStatus.COMPLETED_AUTO))
                    )
                )
                or 0
            )
            outbox_pending = (
                await session.scalar(
                    select(func.count(VkOutbox.id)).where(VkOutbox.status == "pending")
                )
                or 0
            )
            outbox_failed = (
                await session.scalar(
                    select(func.count(VkOutbox.id)).where(VkOutbox.status == "failed")
                )
                or 0
            )
    except Exception as exc:
        db_status = f"🔴 Ошибка ({exc})"

    # 2. Redis статус
    redis_status = "🔴 Отключен"
    try:
        redis_client = await get_redis_client()
        if redis_client:
            pong = await redis_client.ping()
            if pong:
                redis_status = "🟢 OK"
    except Exception as exc:
        redis_status = f"🔴 Ошибка ({exc})"

    text = (
        "📈 <b>Показатели сервиса:</b>\n\n"
        f"🐘 <b>PostgreSQL:</b> {db_status}\n"
        f"⚡ <b>Redis:</b> {redis_status}\n\n"
        f"🎫 <b>Обращения студентов:</b>\n"
        f"• Всего создано: <b>{total_tickets}</b>\n"
        f"• Новых (ждут ответа): <b>{new_tickets}</b>\n"
        f"• В работе у операторов: <b>{in_progress}</b>\n"
        f"• Завершено: <b>{completed}</b>\n\n"
        f"📬 <b>Очередь Outbox:</b>\n"
        f"• Ожидают отправки: <b>{outbox_pending}</b>\n"
        f"• Ошибки доставки (Dead Letter): <b>{outbox_failed}</b>"
    )
    await message.answer(text, keyboard=build_tech_admin_keyboard())


@tech_admin_labeler.private_message(text=["💾 Бэкап БД", "бэкап", "/tech_backup"])
async def tech_backup_handler(message: Message) -> None:
    """Запуск резервного копирования базы данных в изолированном режиме."""
    if not await _check_access(message):
        return

    await message.answer("⏳ Запуск создания резервной копии базы данных...")
    ok, data, filename_or_err, digest = await perform_isolated_database_backup()

    if ok:
        size_str = f"{len(data) / 1024:.1f} КБ" if data else "Н/Д"
        text = (
            "✅ <b>Резервная копия базы данных успешно создана!</b>\n\n"
            f"📁 <b>Файл:</b> <code>{filename_or_err}</code>\n"
            f"📦 <b>Размер:</b> <code>{size_str}</code>\n"
            f"🔐 <b>SHA-256:</b> <code>{digest}</code>\n"
            "🗂 <b>Каталог:</b> <code>/var/backups/oss_bot</code> (права 0600)\n\n"
            "ℹ️ <i>Дамп базы сохранён на сервере и не передаётся во внешние чаты (SEC-07 / 152-ФЗ).</i>"
        )
    else:
        text = f"❌ <b>Ошибка резервного копирования:</b>\n{filename_or_err}"

    await message.answer(text, keyboard=build_tech_admin_keyboard())


@tech_admin_labeler.private_message(text=["🚧 Техработы", "техработы", "/tech_maintenance"])
async def tech_maintenance_handler(message: Message) -> None:
    """Просмотр и переключение режима технических работ."""
    if not await _check_access(message):
        return

    active = await is_maintenance_mode()
    status_text = (
        "🔴 <b>ВКЛЮЧЁН</b> (студенты получают заглушку 503)"
        if active
        else "🟢 <b>ОТКЛЮЧЁН</b> (система работает в штатном режиме)"
    )
    text = (
        f"🚧 <b>Режим технического обслуживания:</b>\n\n"
        f"Текущий статус: {status_text}\n\n"
        "Нажмите кнопку ниже для переключения режима:"
    )
    await message.answer(text, keyboard=build_tech_maintenance_keyboard(active))


@tech_admin_labeler.private_message(text=["🔴 Включить техработы", "🟢 Отключить техработы"])
async def tech_maintenance_toggle_handler(message: Message) -> None:
    """Переключение режима техработ по кнопке."""
    if not await _check_access(message):
        return

    current = await is_maintenance_mode()
    new_state = not current
    await set_maintenance_mode(new_state)

    state_str = "включён 🔴" if new_state else "отключён 🟢"
    await message.answer(
        f"✅ Режим технических работ успешно {state_str}!",
        keyboard=build_tech_admin_keyboard(),
    )


@tech_admin_labeler.private_message(text=["🔄 Контейнеры", "контейнеры", "/tech_containers"])
async def tech_containers_handler(message: Message) -> None:
    """Список контейнеров с возможностью выбора для перезапуска."""
    if not await _check_access(message):
        return

    docker_client = DockerClient()
    names = []
    try:
        containers = await docker_client.list_containers(all=True)
        names = [c.name for c in containers if c.name]
    except Exception as exc:
        logger.debug("Не удалось получить список контейнеров: %s", exc)

    if not names:
        names = [
            "oss_bot_app",
            "oss_bot_web",
            "oss_bot_redis",
            "oss_bot_pgbouncer",
            "oss_bot_go_api",
            "oss_bot_caddy",
        ]

    text = "🔄 <b>Управление контейнерами:</b>\n\nВыберите сервис для перезапуска:"
    await message.answer(text, keyboard=build_tech_containers_keyboard(names))


@tech_admin_labeler.private_message()
async def tech_action_dynamic_handler(message: Message) -> None:
    """Динамическая обработка кнопок перезапуска контейнеров и просмотра логов."""
    raw_text = (message.text or "").strip()

    # Перезапуск контейнера
    restart_match = RESTART_CONTAINER_PATTERN.match(raw_text)
    if restart_match:
        if not await _check_access(message):
            return
        c_name = restart_match.group(1).strip()
        await message.answer(f"⏳ Перезапуск контейнера <code>{c_name}</code>...")
        docker_client = DockerClient()
        ok, res_msg = await docker_client.restart_container(c_name)
        if ok:
            await message.answer(
                f"✅ Контейнер <code>{c_name}</code> успешно перезапущен!",
                keyboard=build_tech_admin_keyboard(),
            )
        else:
            await message.answer(
                f"❌ Ошибка перезапуска <code>{c_name}</code>:\n{res_msg}",
                keyboard=build_tech_admin_keyboard(),
            )
        return

    # Просмотр логов контейнера
    logs_match = LOGS_CONTAINER_PATTERN.match(raw_text)
    if logs_match:
        if not await _check_access(message):
            return
        c_name = logs_match.group(1).strip()
        docker_client = DockerClient()
        logs_text = await docker_client.get_container_logs(c_name, tail=25)
        clean_logs = logs_text[-2000:] if len(logs_text) > 2000 else logs_text
        if not clean_logs.strip():
            clean_logs = "(Логи пусты)"
        await message.answer(
            f"📋 <b>Последние логи {c_name}:</b>\n\n<pre>{clean_logs}</pre>",
            keyboard=build_tech_admin_keyboard(),
        )
        return


@tech_admin_labeler.private_message(text=["🚪 Сброс сессий", "kickall", "/tech_kickall"])
async def tech_kickall_handler(message: Message) -> None:
    """Экстренный сброс всех активных сессий пользователей в Redis."""
    if not await _check_access(message):
        return

    redis_client = await get_redis_client()
    if not redis_client:
        await message.answer(
            "⚠️ Redis недоступен, сброс сессий невозможен.", keyboard=build_tech_admin_keyboard()
        )
        return

    try:
        keys = [key async for key in redis_client.scan_iter("session:*")]

        if keys:
            await redis_client.delete(*keys)
            count = len(keys)
        else:
            count = 0

        text = (
            f"🚪 <b>Сброс активных сессий завершён!</b>\n\n"
            f"Удалено активных сессий: <b>{count}</b>\n"
            "Всем операторам и администраторам потребуется повторный вход."
        )
    except Exception as exc:
        text = f"❌ Ошибка при сбросе сессий в Redis: {exc}"

    await message.answer(text, keyboard=build_tech_admin_keyboard())


@tech_admin_labeler.private_message(text=["📋 Логи сервисов", "логи", "/tech_logs"])
async def tech_logs_menu_handler(message: Message) -> None:
    """Меню выбора сервиса для просмотра последних логов."""
    if not await _check_access(message):
        return

    text = "📋 <b>Просмотр логов:</b>\n\nВыберите сервис для чтения последних строк вывода:"
    await message.answer(text, keyboard=build_tech_logs_keyboard())


@tech_admin_labeler.private_message(text=["🔐 2FA статус", "2fa", "/tech_2fa"])
async def tech_2fa_handler(message: Message) -> None:
    """Просмотр глобального статуса двухфакторной аутентификации."""
    if not await _check_access(message):
        return

    state_str = (
        "🟢 Включена (обязательна в продакшене)" if settings.TWO_FACTOR_ENABLED else "🟡 Отключена"
    )
    text = (
        f"🔐 <b>Двухфакторная аутентификация (2FA):</b>\n\n"
        f"Глобальный статус: <b>{state_str}</b>\n"
        f"Служба доставки кодов: личные сообщения VK / Telegram\n"
        "Время жизни одноразового пароля (OTP): 180 секунд"
    )
    await message.answer(text, keyboard=build_tech_admin_keyboard())


@tech_admin_labeler.private_message(text=["Главное меню", "Обычное меню", "/menu", "меню"])
async def tech_return_main_menu(message: Message) -> None:
    """Возврат в обычное главное меню."""
    kb = await _main_keyboard_for(message.from_id)
    await message.answer("Вы вернулись в главное меню бота.", keyboard=kb)
