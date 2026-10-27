"""Хендлеры Технического администратора для VK-бота.

Предоставляет доступ к управлению сервером, контейнерами, бэкапами и
системными метриками. Доступ строго ограничен TECH_ADMIN_VK_ID из .env.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from vkbottle.bot import BotLabeler, Message
from vkbottle.dispatch.rules.base import RegexRule

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
from core.commands import COMMANDS_TECH_PANEL
from core.config import settings
from core.database import async_session_maker
from core.maintenance import is_maintenance_mode, set_maintenance_mode
from core.models import Ticket, TicketStatus, VkOutbox
from core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

tech_admin_labeler = BotLabeler()

# Регулярные выражения для команд действий
RESTART_CONTAINER_PATTERN = re.compile(
    r"^(?:🔄\s*)?перезапуск\s+([a-zA-Z0-9_\-]+)$", re.IGNORECASE
)
LOGS_CONTAINER_PATTERN = re.compile(r"^(?:📋\s*)?логи\s+([a-zA-Z0-9_\-]+)$", re.IGNORECASE)


def is_tech_admin(vk_id: int | None) -> bool:
    """Проверка прав: доступ разрешён исключительно TECH_ADMIN_VK_ID."""
    return BotCore.is_tech_admin_vk_id(vk_id)


async def _check_access(message: Message) -> bool:
    """Проверить доступ: если не тех-админ — тихо отбросить запрос."""
    if not is_tech_admin(message.from_id):
        logger.warning(
            "Отклонён неавторизованный запрос к тех-панели VK от vk_id=%s", message.from_id
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
        "Панель Технического Администратора\n\n"
        f"Текущее время: {now_str}\n"
        f"Версия: v{settings.APP_VERSION}\n"
        f"Доступ подтверждён для VK ID: {message.from_id}\n\n"
        "Выберите раздел для мониторинга или управления сервисами:"
    )
    await message.answer(text, keyboard=build_tech_admin_keyboard())


@tech_admin_labeler.private_message(text=["Сервер", "сервер", "📊 Сервер", "/tech_status"])
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
                icon = "[UP]" if "up" in c.status.lower() else "[DOWN]"
                container_lines.append(f"{icon} {c.name} ({c.status})")
        else:
            container_lines.append("Контейнеры не обнаружены или сокет недоступен")
    except Exception as exc:
        container_lines.append(f"Ошибка опроса Docker: {exc}")

    containers_block = "\n".join(container_lines) if container_lines else "Н/Д"

    text = (
        "Состояние сервера и инфраструктуры:\n\n"
        f"Uptime: {uptime}\n\n"
        f"CPU: {render_progress_bar(cpu_pct)} {cpu_pct:.1f}%\n"
        f"RAM: {render_progress_bar(ram_pct)} {ram_used} / {ram_total} ({ram_pct:.1f}%)\n"
        f"Диск: {render_progress_bar(disk_pct)} {disk_used} / {disk_total} ({disk_pct:.1f}%)\n\n"
        f"Контейнеры:\n{containers_block}"
    )
    await message.answer(text, keyboard=build_tech_admin_keyboard())


@tech_admin_labeler.private_message(text=["Метрики", "метрики", "📈 Метрики", "/tech_metrics"])
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
    db_status = "OK"

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
        db_status = f"Ошибка ({exc})"

    # 2. Redis статус
    redis_status = "Отключен"
    try:
        redis_client = await get_redis_client()
        if redis_client:
            pong = await redis_client.ping()
            if pong:
                redis_status = "OK"
    except Exception as exc:
        redis_status = f"Ошибка ({exc})"

    text = (
        "Показатели сервиса:\n\n"
        f"PostgreSQL: {db_status}\n"
        f"Redis: {redis_status}\n\n"
        "Обращения студентов:\n"
        f"• Всего создано: {total_tickets}\n"
        f"• Новых (ждут ответа): {new_tickets}\n"
        f"• В работе у операторов: {in_progress}\n"
        f"• Завершено: {completed}\n\n"
        "Очередь Outbox:\n"
        f"• Ожидают отправки: {outbox_pending}\n"
        f"• Ошибки доставки (Dead Letter): {outbox_failed}"
    )
    await message.answer(text, keyboard=build_tech_admin_keyboard())


@tech_admin_labeler.private_message(
    text=["Бэкап БД", "бэкап бд", "Бэкап", "бэкап", "💾 Бэкап БД", "/tech_backup"]
)
async def tech_backup_handler(message: Message) -> None:
    """Запуск резервного копирования базы данных в изолированном режиме."""
    if not await _check_access(message):
        return

    await message.answer("Запуск создания резервной копии базы данных...")
    ok, data, filename_or_err, digest = await perform_isolated_database_backup()

    if ok:
        size_str = f"{len(data) / 1024:.1f} КБ" if data else "Н/Д"
        text = (
            "Резервная копия базы данных успешно создана.\n\n"
            f"Файл: {filename_or_err}\n"
            f"Размер: {size_str}\n"
            f"SHA-256: {digest}\n"
            "Каталог: /var/backups/oss_bot (права 0600)\n\n"
            "Дамп базы сохранён на сервере и не передаётся во внешние чаты (SEC-07 / 152-ФЗ)."
        )
    else:
        text = f"Ошибка резервного копирования:\n{filename_or_err}"

    await message.answer(text, keyboard=build_tech_admin_keyboard())


@tech_admin_labeler.private_message(
    text=["Техработы", "техработы", "🚧 Техработы", "/tech_maintenance"]
)
async def tech_maintenance_handler(message: Message) -> None:
    """Просмотр и переключение режима технических работ."""
    if not await _check_access(message):
        return

    active = await is_maintenance_mode()
    status_text = (
        "ВКЛЮЧЁН (студенты получают заглушку 503)"
        if active
        else "ОТКЛЮЧЁН (система работает в штатном режиме)"
    )
    text = (
        "Режим технического обслуживания:\n\n"
        f"Текущий статус: {status_text}\n\n"
        "Нажмите кнопку ниже для переключения режима:"
    )
    await message.answer(text, keyboard=build_tech_maintenance_keyboard(active))


@tech_admin_labeler.private_message(
    text=[
        "Включить техработы",
        "включить техработы",
        "Отключить техработы",
        "отключить техработы",
        "🔴 Включить техработы",
        "🟢 Отключить техработы",
    ]
)
async def tech_maintenance_toggle_handler(message: Message) -> None:
    """Переключение режима техработ по кнопке."""
    if not await _check_access(message):
        return

    current = await is_maintenance_mode()
    new_state = not current
    await set_maintenance_mode(new_state)

    state_str = "включён" if new_state else "отключён"
    await message.answer(
        f"Режим технических работ успешно {state_str}.",
        keyboard=build_tech_admin_keyboard(),
    )


@tech_admin_labeler.private_message(
    text=["Контейнеры", "контейнеры", "🔄 Контейнеры", "/tech_containers"]
)
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

    text = "Управление контейнерами:\n\nВыберите сервис для перезапуска:"
    await message.answer(text, keyboard=build_tech_containers_keyboard(names))


@tech_admin_labeler.private_message(RegexRule(r"(?i)^(?:🔄\s*)?перезапуск\s+([a-zA-Z0-9_\-]+)$"))
async def tech_restart_container_handler(message: Message) -> None:
    """Перезапуск контейнера по кнопке или команде."""
    if not await _check_access(message):
        return
    raw_text = (message.text or "").strip()
    restart_match = RESTART_CONTAINER_PATTERN.match(raw_text)
    if not restart_match:
        return
    c_name = restart_match.group(1).strip()
    await message.answer(f"Перезапуск контейнера {c_name}...")
    docker_client = DockerClient()
    ok, res_msg = await docker_client.restart_container(c_name)
    if ok:
        await message.answer(
            f"Контейнер {c_name} успешно перезапущен.",
            keyboard=build_tech_admin_keyboard(),
        )
    else:
        await message.answer(
            f"Ошибка перезапуска {c_name}:\n{res_msg}",
            keyboard=build_tech_admin_keyboard(),
        )


@tech_admin_labeler.private_message(RegexRule(r"(?i)^(?:📋\s*)?логи\s+([a-zA-Z0-9_\-]+)$"))
async def tech_logs_container_handler(message: Message) -> None:
    """Просмотр логов контейнера по кнопке или команде."""
    if not await _check_access(message):
        return
    raw_text = (message.text or "").strip()
    logs_match = LOGS_CONTAINER_PATTERN.match(raw_text)
    if not logs_match:
        return
    c_name = logs_match.group(1).strip()
    docker_client = DockerClient()
    logs_text = await docker_client.get_container_logs(c_name, tail=25)
    clean_logs = logs_text[-2000:] if len(logs_text) > 2000 else logs_text
    if not clean_logs.strip():
        clean_logs = "(Логи пусты)"
    await message.answer(
        f"Последние логи {c_name}:\n\n{clean_logs}",
        keyboard=build_tech_admin_keyboard(),
    )


async def tech_action_dynamic_handler(message: Message) -> None:
    """Совместимый диспетчер команд перезапуска и логов (для прямого вызова)."""
    raw_text = (message.text or "").strip()
    if RESTART_CONTAINER_PATTERN.match(raw_text):
        await tech_restart_container_handler(message)
    elif LOGS_CONTAINER_PATTERN.match(raw_text):
        await tech_logs_container_handler(message)


@tech_admin_labeler.private_message(
    text=["Сброс сессий", "сброс сессий", "🚪 Сброс сессий", "kickall", "/tech_kickall"]
)
async def tech_kickall_handler(message: Message) -> None:
    """Экстренный сброс всех активных сессий пользователей в Redis."""
    if not await _check_access(message):
        return

    redis_client = await get_redis_client()
    if not redis_client:
        await message.answer(
            "Redis недоступен, сброс сессий невозможен.", keyboard=build_tech_admin_keyboard()
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
            "Сброс активных сессий завершён.\n\n"
            f"Удалено активных сессий: {count}\n"
            "Всем операторам и администраторам потребуется повторный вход."
        )
    except Exception as exc:
        text = f"Ошибка при сбросе сессий в Redis: {exc}"

    await message.answer(text, keyboard=build_tech_admin_keyboard())


@tech_admin_labeler.private_message(
    text=["Логи сервисов", "логи сервисов", "Логи", "логи", "📋 Логи сервисов", "/tech_logs"]
)
async def tech_logs_menu_handler(message: Message) -> None:
    """Меню выбора сервиса для просмотра последних логов."""
    if not await _check_access(message):
        return

    text = "Просмотр логов:\n\nВыберите сервис для чтения последних строк вывода:"
    await message.answer(text, keyboard=build_tech_logs_keyboard())


@tech_admin_labeler.private_message(
    text=["2FA статус", "2fa статус", "2FA", "2fa", "🔐 2FA статус", "/tech_2fa"]
)
async def tech_2fa_handler(message: Message) -> None:
    """Просмотр глобального статуса двухфакторной аутентификации."""
    if not await _check_access(message):
        return

    state_str = (
        "Включена (обязательна в продакшене)" if settings.TWO_FACTOR_ENABLED else "Отключена"
    )
    text = (
        "Двухфакторная аутентификация (2FA):\n\n"
        f"Глобальный статус: {state_str}\n"
        "Служба доставки кодов: личные сообщения VK / Telegram\n"
        "Время жизни одноразового пароля (OTP): 180 секунд"
    )
    await message.answer(text, keyboard=build_tech_admin_keyboard())


@tech_admin_labeler.private_message(
    text=["Главное меню", "главное меню", "Обычное меню", "обычное меню", "/menu", "меню", "Меню"]
)
async def tech_return_main_menu(message: Message) -> None:
    """Возврат в обычное главное меню."""
    kb = await _main_keyboard_for(message.from_id)
    await message.answer("Вы вернулись в главное меню бота.", keyboard=kb)
