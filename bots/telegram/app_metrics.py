"""Сбор бизнес-метрик сайта и VK-бота для Telegram-мониторинга.

В отличие от `system_metrics.py` (CPU/RAM/Диск/Uptime), этот модуль отвечает
на вопрос «что происходит в продукте»:

Статистика сайта (веб-панель):
- администраторы онлайн (активные сессии за 15 минут в Redis);
- новые / в обработке / нераспределённые (без отдела) заявки;
- успешно решённые заявки за сегодня и за всё время;
- партнёрские заявки (новые и ожидающие связи);
- синхронизация времени и health-check веб-сервиса.

Статистика бота (VK):
- всего пользователей и новых за сегодня;
- состояние очереди outbox (pending / failed);
- сообщения диалога за сутки.

Все запросы идут через `async_session_maker`, поэтому фикстуры тестов
подменяют соединение на in-memory SQLite автоматически (tests/conftest.py).
"""

from __future__ import annotations

import html
import logging
from typing import Any

from sqlalchemy import func, select

from core.admin_presence import count_online
from core.models import (
    PartnershipRequest,
    Ticket,
    TicketMessage,
    TicketStatus,
    User,
    VkOutbox,
)
from core.time_utils import check_time_sync, now_app_tz

logger = logging.getLogger(__name__)

# Статусы, которые считаются «успешно решёнными».
COMPLETED_STATUSES = (TicketStatus.COMPLETED, TicketStatus.COMPLETED_AUTO)


def _session_maker():
    """Получить актуальный session maker.

    Обращаемся к core.database в момент вызова, а не к импортированному
    имени: так подмена соединения в тестах (monkeypatch по атрибуту модуля)
    применяется к этому сервису автоматически, без правки conftest.
    """
    from core import database

    return database.async_session_maker()


def day_start_in_app_tz():
    """Начало сегодняшних суток в часовом поясе приложения.

    Граница суток считается по МСК (APP_TIMEZONE), а не по UTC: «сегодня»
    для администратора должно совпадать с его календарём.
    """
    return now_app_tz().replace(hour=0, minute=0, second=0, microsecond=0)


async def _count(session, stmt) -> int:
    """Безопасно посчитать строки по запросу (0 при NULL)."""
    return int((await session.scalar(stmt)) or 0)


async def collect_site_metrics() -> dict[str, Any]:
    """Бизнес-метрики веб-панели: заявки, партнёрства, присутствие админов."""
    metrics: dict[str, Any] = {
        "admins_online": 0,
        "tickets_new": 0,
        "tickets_in_progress": 0,
        "tickets_unassigned": 0,
        "tickets_unassigned_new": 0,
        "tickets_completed_today": 0,
        "tickets_completed_total": 0,
        "partnerships_new": 0,
        "partnerships_pending": 0,
        "db_error": None,
    }

    try:
        metrics["admins_online"] = await count_online()
    except Exception as exc:  # pragma: no cover - зависит от состояния Redis
        logger.debug("Не удалось посчитать администраторов онлайн: %s", exc)

    try:
        day_start = day_start_in_app_tz()
        async with _session_maker() as session:
            metrics["tickets_new"] = await _count(
                session, select(func.count(Ticket.id)).where(Ticket.status == TicketStatus.NEW)
            )
            metrics["tickets_in_progress"] = await _count(
                session,
                select(func.count(Ticket.id)).where(Ticket.status == TicketStatus.IN_PROGRESS),
            )
            # Нераспределённые: department_id IS NULL — «Общие вопросы».
            metrics["tickets_unassigned"] = await _count(
                session, select(func.count(Ticket.id)).where(Ticket.department_id.is_(None))
            )
            metrics["tickets_unassigned_new"] = await _count(
                session,
                select(func.count(Ticket.id))
                .where(Ticket.department_id.is_(None))
                .where(Ticket.status == TicketStatus.NEW),
            )
            metrics["tickets_completed_total"] = await _count(
                session,
                select(func.count(Ticket.id)).where(Ticket.status.in_(COMPLETED_STATUSES)),
            )
            metrics["tickets_completed_today"] = await _count(
                session,
                select(func.count(Ticket.id))
                .where(Ticket.status.in_(COMPLETED_STATUSES))
                .where(Ticket.updated_at >= day_start),
            )
            metrics["partnerships_new"] = await _count(
                session,
                select(func.count(PartnershipRequest.id)).where(
                    PartnershipRequest.status == "new"
                ),
            )
            metrics["partnerships_pending"] = await _count(
                session,
                select(func.count(PartnershipRequest.id)).where(
                    PartnershipRequest.status.in_(("new", "contacted"))
                ),
            )
    except Exception as exc:
        logger.warning("Не удалось собрать метрики сайта: %s", exc)
        metrics["db_error"] = str(exc)

    return metrics


async def collect_bot_metrics() -> dict[str, Any]:
    """Метрики VK-бота: пользователи, очередь outbox, сообщения диалога."""
    metrics: dict[str, Any] = {
        "users_total": 0,
        "users_today": 0,
        "outbox_pending": 0,
        "outbox_failed": 0,
        "outbox_sent_today": 0,
        "dialog_messages_today": 0,
        "db_error": None,
    }

    try:
        day_start = day_start_in_app_tz()
        async with _session_maker() as session:
            metrics["users_total"] = await _count(session, select(func.count(User.id)))
            metrics["users_today"] = await _count(
                session, select(func.count(User.id)).where(User.created_at >= day_start)
            )
            metrics["outbox_pending"] = await _count(
                session, select(func.count(VkOutbox.id)).where(VkOutbox.status == "pending")
            )
            metrics["outbox_failed"] = await _count(
                session, select(func.count(VkOutbox.id)).where(VkOutbox.status == "failed")
            )
            metrics["outbox_sent_today"] = await _count(
                session,
                select(func.count(VkOutbox.id))
                .where(VkOutbox.status == "sent")
                .where(VkOutbox.sent_at >= day_start),
            )
            metrics["dialog_messages_today"] = await _count(
                session,
                select(func.count(TicketMessage.id)).where(TicketMessage.created_at >= day_start),
            )
    except Exception as exc:
        logger.warning("Не удалось собрать метрики бота: %s", exc)
        metrics["db_error"] = str(exc)

    return metrics


async def collect_infra_metrics() -> dict[str, Any]:
    """Синхронизация времени и доступность веб-сервиса."""
    result: dict[str, Any] = {"time_sync": None, "db_error": None}
    try:
        async with _session_maker() as session:
            result["time_sync"] = await check_time_sync(session)
    except Exception as exc:
        logger.warning("Не удалось проверить синхронизацию времени: %s", exc)
        result["db_error"] = str(exc)
    return result


async def collect_app_metrics() -> dict[str, Any]:
    """Собрать все бизнес-метрики сайта и бота одним вызовом."""
    site = await collect_site_metrics()
    bot = await collect_bot_metrics()
    infra = await collect_infra_metrics()
    return {"site": site, "bot": bot, "infra": infra}


def _icon_for_count(count: int, warn_at: int, danger_at: int) -> str:
    """Иконка-индикатор по величине показателя."""
    if count >= danger_at:
        return "🔴"
    if count >= warn_at:
        return "🟡"
    return "🟢"


def format_time_sync_line(time_sync: dict[str, Any] | None, db_error: str | None) -> str:
    """Строка статуса синхронизации времени приложения и СУБД."""
    if db_error:
        return f"🔴 <b>Время:</b> проверить не удалось ({html.escape(str(db_error)[:80])})"
    if not time_sync:
        return "⚪ <b>Время:</b> нет данных"

    if time_sync.get("synchronized"):
        return "🟢 <b>Время:</b> синхронизировано"

    if time_sync.get("status") == "error":
        detail = html.escape(str(time_sync.get("detail", ""))[:80])
        return f"🔴 <b>Время:</b> ошибка проверки ({detail})"

    drift = time_sync.get("drift_seconds", "?")
    return f"🟡 <b>Время:</b> расхождение {drift} с"


def format_app_metrics_message(metrics: dict[str, Any]) -> str:
    """Сформировать HTML-сообщение с показателями сайта и бота."""
    site = metrics.get("site") or {}
    bot = metrics.get("bot") or {}
    infra = metrics.get("infra") or {}

    if site.get("db_error") and bot.get("db_error"):
        detail = html.escape(str(site["db_error"])[:200])
        return (
            "📊 <b>Показатели сайта и бота</b>\n\n"
            f"🔴 <b>База данных недоступна</b>\n<code>{detail}</code>\n\n"
            "Проверьте состояние контейнера <code>oss_bot_db</code>."
        )

    unassigned = int(site.get("tickets_unassigned", 0))
    unassigned_new = int(site.get("tickets_unassigned_new", 0))
    outbox_pending = int(bot.get("outbox_pending", 0))
    outbox_failed = int(bot.get("outbox_failed", 0))
    partnerships_new = int(site.get("partnerships_new", 0))

    lines = [
        "📊 <b>Показатели сайта и бота</b>",
        "",
        "🌐 <b>Веб-панель</b>",
        (
            f"👥 <b>Администраторов онлайн:</b> "
            f"{_icon_for_count(int(site.get('admins_online', 0)), 1, 3)} "
            f"{site.get('admins_online', 0)} <i>(активны за 15 мин)</i>"
        ),
        (
            f"🔥 <b>Новые заявки:</b> "
            f"{_icon_for_count(int(site.get('tickets_new', 0)), 5, 15)} "
            f"{site.get('tickets_new', 0)}"
        ),
        f"⏳ <b>В обработке:</b> {site.get('tickets_in_progress', 0)}",
        (
            f"📌 <b>Общие (без отдела):</b> "
            f"{_icon_for_count(unassigned_new, 3, 10)} {unassigned}"
            f" <i>(из них новых: {unassigned_new})</i>"
        ),
        (
            f"✅ <b>Решено сегодня:</b> {site.get('tickets_completed_today', 0)}"
            f" <i>(за всё время: {site.get('tickets_completed_total', 0)})</i>"
        ),
        (
            f"🤝 <b>Партнёрские заявки:</b> "
            f"{_icon_for_count(partnerships_new, 1, 5)} новых {partnerships_new}"
            f" <i>(ожидают связи: {site.get('partnerships_pending', 0)})</i>"
        ),
        "",
        "🤖 <b>VK-бот</b>",
        (
            f"🎓 <b>Студентов в базе:</b> {bot.get('users_total', 0)}"
            f" <i>(новых сегодня: {bot.get('users_today', 0)})</i>"
        ),
        (
            f"📮 <b>Очередь Outbox:</b> "
            f"{_icon_for_count(outbox_failed, 1, 10)} ожидают {outbox_pending}"
            f" <i>(сбоев: {outbox_failed})</i>"
        ),
        (
            f"💬 <b>Сообщений диалога за сутки:</b> {bot.get('dialog_messages_today', 0)}"
            f" <i>(отправлено VK: {bot.get('outbox_sent_today', 0)})</i>"
        ),
        "",
        format_time_sync_line(infra.get("time_sync"), infra.get("db_error")),
    ]

    return "\n".join(lines)


__all__ = [
    "collect_app_metrics",
    "collect_bot_metrics",
    "collect_infra_metrics",
    "collect_site_metrics",
    "day_start_in_app_tz",
    "format_app_metrics_message",
    "format_time_sync_line",
]
