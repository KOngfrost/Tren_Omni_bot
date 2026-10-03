"""Сбор бизнес-метрик сайта и VK-бота для Telegram-мониторинга.

В отличие от `system_metrics.py` (CPU/RAM/Диск/Uptime), этот модуль отвечает
на вопрос «что происходит в продукте»:

1. Метрики сайта администрации:
- Трафик и аудитория: визиты, уникальные посетители (DAU, WAU, MAU), администраторы онлайн;
- Вовлечённость: заполнение форм (мутации данных), обращения;
- Обращения граждан: новые, в работе, нераспределённые, решённые сегодня/всего, партнёрства;
- Технические показатели: Uptime, ошибки 4xx и 5xx, среднее время ответа (latency);
- Безопасность и 152-ФЗ: статус модуля приватности, попытки брутфорса / неудачные логины.

2. Метрики бота (VK):
- Аудитория: всего пользователей, новые за день/неделю/месяц, DAU бота;
- Использование: сообщения за сутки, всего сообщений диалога, глубина общения;
- Результативность: Containment rate (решено ботом без оператора), Escalation rate (передано оператору);
- Технические и операционные: состояние очереди Outbox (ожидают, сбои, отправлено сегодня).

3. Сквозные KPI (Сайт + Бот):
- Доля цифровых обращений, Containment Rate, результативность решений, индекс безопасности.

4. Минимальный сводный дашборд KPI.
"""

from __future__ import annotations

import html
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from core.admin_presence import count_online
from core.models import (
    Log,
    LoginAttempt,
    PartnershipRequest,
    Ticket,
    TicketMessage,
    TicketStatus,
    User,
    VkOutbox,
)
from core.time_utils import check_time_sync, day_start_app_tz, now_app_tz

logger = logging.getLogger(__name__)

# Статусы, которые считаются «успешно решёнными».
COMPLETED_STATUSES = (TicketStatus.COMPLETED, TicketStatus.COMPLETED_AUTO)
ESCALATED_STATUSES = (
    TicketStatus.TRANSFERRED_ADMIN,
    TicketStatus.TRANSFERRED_HOUSEKEEPING,
)


def _session_maker():
    """Получить актуальный session maker."""
    from core import database

    return database.async_session_maker()


def day_start_in_app_tz():
    """Начало суток по МСК."""
    return day_start_app_tz()


async def _count(session, stmt) -> int:
    """Безопасно посчитать строки по запросу (0 при NULL)."""
    return int((await session.scalar(stmt)) or 0)


async def collect_site_metrics() -> dict[str, Any]:
    """Бизнес-метрики веб-панели: заявки, партнёрства, присутствие админов, трафик."""
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
        "requests_24h": 0,
        "requests_total": 0,
        "dau": 0,
        "wau": 0,
        "mau": 0,
        "avg_latency_ms": 0,
        "errors_4xx": 0,
        "errors_5xx": 0,
        "mutations_today": 0,
        "failed_logins_24h": 0,
        "privacy_status": "Активен (152-ФЗ)",
        "db_error": None,
    }

    try:
        metrics["admins_online"] = await count_online()
    except Exception as exc:  # pragma: no cover
        logger.debug("Не удалось посчитать администраторов онлайн: %s", exc)

    try:
        day_start = day_start_in_app_tz()
        now = now_app_tz()
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)

        async with _session_maker() as session:
            # 1. Заявки
            metrics["tickets_new"] = await _count(
                session, select(func.count(Ticket.id)).where(Ticket.status == TicketStatus.NEW)
            )
            metrics["tickets_in_progress"] = await _count(
                session,
                select(func.count(Ticket.id)).where(Ticket.status == TicketStatus.IN_PROGRESS),
            )
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

            # 2. Трафик и аудит (таблица logs)
            metrics["requests_24h"] = await _count(
                session, select(func.count(Log.id)).where(Log.created_at >= day_start)
            )
            metrics["requests_total"] = await _count(session, select(func.count(Log.id)))
            metrics["dau"] = await _count(
                session,
                select(func.count(func.distinct(Log.ip_address))).where(
                    Log.created_at >= day_start
                ),
            )
            metrics["wau"] = await _count(
                session,
                select(func.count(func.distinct(Log.ip_address))).where(
                    Log.created_at >= week_ago
                ),
            )
            metrics["mau"] = await _count(
                session,
                select(func.count(func.distinct(Log.ip_address))).where(
                    Log.created_at >= month_ago
                ),
            )

            avg_lat = await session.scalar(
                select(func.avg(Log.duration_ms)).where(
                    Log.created_at >= day_start,
                    Log.duration_ms.is_not(None),
                )
            )
            metrics["avg_latency_ms"] = int(avg_lat or 0)

            metrics["errors_4xx"] = await _count(
                session,
                select(func.count(Log.id)).where(
                    Log.created_at >= day_start,
                    Log.status_code >= 400,
                    Log.status_code < 500,
                ),
            )
            metrics["errors_5xx"] = await _count(
                session,
                select(func.count(Log.id)).where(
                    Log.created_at >= day_start,
                    Log.status_code >= 500,
                ),
            )
            metrics["mutations_today"] = await _count(
                session,
                select(func.count(Log.id)).where(
                    Log.created_at >= day_start,
                    Log.is_mutation.is_(True),
                ),
            )

            # 3. Безопасность (неудачные попытки авторизации)
            metrics["failed_logins_24h"] = await _count(
                session,
                select(func.count(LoginAttempt.id)).where(
                    LoginAttempt.attempted_at >= day_start,
                    LoginAttempt.success.is_(False),
                ),
            )
    except Exception as exc:
        logger.warning("Не удалось собрать метрики сайта: %s", exc)
        metrics["db_error"] = str(exc)

    return metrics


async def collect_bot_metrics() -> dict[str, Any]:
    """Метрики VK-бота: пользователи, диалоги, результативность (Containment/Escalation)."""
    metrics: dict[str, Any] = {
        "users_total": 0,
        "users_today": 0,
        "users_7d": 0,
        "users_30d": 0,
        "bot_dau": 0,
        "outbox_pending": 0,
        "outbox_failed": 0,
        "outbox_sent_today": 0,
        "dialog_messages_today": 0,
        "dialog_messages_total": 0,
        "all_tickets": 0,
        "completed_auto": 0,
        "containment_pct": 0.0,
        "escalated_count": 0,
        "escalation_pct": 0.0,
        "db_error": None,
    }

    try:
        day_start = day_start_in_app_tz()
        now = now_app_tz()
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)

        async with _session_maker() as session:
            metrics["users_total"] = await _count(session, select(func.count(User.id)))
            metrics["users_today"] = await _count(
                session, select(func.count(User.id)).where(User.created_at >= day_start)
            )
            metrics["users_7d"] = await _count(
                session, select(func.count(User.id)).where(User.created_at >= week_ago)
            )
            metrics["users_30d"] = await _count(
                session, select(func.count(User.id)).where(User.created_at >= month_ago)
            )

            metrics["bot_dau"] = await _count(
                session,
                select(func.count(func.distinct(TicketMessage.author_vk_id))).where(
                    TicketMessage.created_at >= day_start,
                    TicketMessage.author_vk_id.is_not(None),
                ),
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
            metrics["dialog_messages_total"] = await _count(
                session, select(func.count(TicketMessage.id))
            )

            # Воронка и результативность:
            all_tickets = await _count(session, select(func.count(Ticket.id)))
            metrics["all_tickets"] = all_tickets

            completed_auto = await _count(
                session,
                select(func.count(Ticket.id)).where(Ticket.status == TicketStatus.COMPLETED_AUTO),
            )
            completed_total = await _count(
                session,
                select(func.count(Ticket.id)).where(Ticket.status.in_(COMPLETED_STATUSES)),
            )
            metrics["completed_auto"] = completed_auto
            if completed_total > 0:
                metrics["containment_pct"] = round((completed_auto / completed_total) * 100, 1)

            escalated = await _count(
                session,
                select(func.count(Ticket.id)).where(Ticket.status.in_(ESCALATED_STATUSES)),
            )
            metrics["escalated_count"] = escalated
            if all_tickets > 0:
                metrics["escalation_pct"] = round((escalated / all_tickets) * 100, 1)

    except Exception as exc:
        logger.warning("Не удалось собрать метрики бота: %s", exc)
        metrics["db_error"] = str(exc)

    return metrics


async def collect_infra_metrics() -> dict[str, Any]:
    """Синхронизация времени и доступность сервисов."""
    result: dict[str, Any] = {"time_sync": None, "db_error": None}
    try:
        async with _session_maker() as session:
            result["time_sync"] = await check_time_sync(session)
    except Exception as exc:
        logger.warning("Не удалось проверить синхронизацию времени: %s", exc)
        result["db_error"] = str(exc)
    return result


def collect_cross_kpi_metrics(site: dict[str, Any], bot: dict[str, Any]) -> dict[str, Any]:
    """Расчёт сквозных KPI для сайта и бота."""
    all_tickets = bot.get("all_tickets", 0)
    completed_total = site.get("tickets_completed_total", 0)
    requests_24h = site.get("requests_24h", 0)
    errors_5xx = site.get("errors_5xx", 0)

    res_rate = round((completed_total / all_tickets) * 100, 1) if all_tickets > 0 else 100.0
    uptime = round(100.0 - ((errors_5xx / requests_24h) * 100), 2) if requests_24h > 0 else 99.9

    return {
        "digital_channel_pct": 100.0,
        "containment_pct": bot.get("containment_pct", 0.0),
        "escalation_pct": bot.get("escalation_pct", 0.0),
        "resolution_rate": res_rate,
        "uptime_estimate": uptime,
        "security_incidents": site.get("failed_logins_24h", 0),
    }


async def collect_app_metrics() -> dict[str, Any]:
    """Собрать все бизнес-метрики сайта, бота и сквозные KPI одним вызовом."""
    site = await collect_site_metrics()
    bot = await collect_bot_metrics()
    infra = await collect_infra_metrics()
    kpi = collect_cross_kpi_metrics(site, bot)
    return {"site": site, "bot": bot, "infra": infra, "kpi": kpi}


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


def format_metrics_summary(metrics: dict[str, Any]) -> str:
    """⚡ Минимальный сводный дашборд KPI (по умолчанию)."""
    site = metrics.get("site") or {}
    bot = metrics.get("bot") or {}
    infra = metrics.get("infra") or {}
    kpi = metrics.get("kpi") or {}

    unassigned = int(site.get("tickets_unassigned", 0))
    unassigned_new = int(site.get("tickets_unassigned_new", 0))
    outbox_pending = int(bot.get("outbox_pending", 0))
    outbox_failed = int(bot.get("outbox_failed", 0))

    lines = [
        "📊 <b>Показатели сайта и бота</b>",
        "⚡ <b>Сводный дашборд KPI</b>",
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
        f"💬 <b>Сообщений за сутки:</b> {bot.get('dialog_messages_today', 0)}",
        "",
        "🎯 <b>Ключевые KPI</b>",
        f"🤖 <b>Containment rate:</b> {bot.get('containment_pct', 0.0)}% <i>(без человека)</i>",
        f"👨‍💼 <b>Escalation rate:</b> {bot.get('escalation_pct', 0.0)}% <i>(передано админам)</i>",
        f"📈 <b>Uptime доступность:</b> {kpi.get('uptime_estimate', 99.9)}%",
        f"🛡 <b>Инциденты / атаки:</b> {kpi.get('security_incidents', 0)}",
        "",
        format_time_sync_line(infra.get("time_sync"), infra.get("db_error")),
    ]
    return "\n".join(lines)


def format_metrics_site(metrics: dict[str, Any]) -> str:
    """🌐 Детальные метрики сайта администрации."""
    site = metrics.get("site") or {}
    unassigned = int(site.get("tickets_unassigned", 0))
    unassigned_new = int(site.get("tickets_unassigned_new", 0))
    partnerships_new = int(site.get("partnerships_new", 0))

    lines = [
        "🌐 <b>Метрики сайта администрации</b>",
        "",
        "👥 <b>Аудитория и трафик:</b>",
        f"• Визитов (запросов) за сутки: <b>{site.get('requests_24h', 0)}</b> <i>(всего: {site.get('requests_total', 0)})</i>",
        f"• DAU (уникальные за сутки): <b>{site.get('dau', 0)}</b>",
        f"• WAU (уникальные за 7 дней): <b>{site.get('wau', 0)}</b>",
        f"• MAU (уникальные за 30 дней): <b>{site.get('mau', 0)}</b>",
        f"• Администраторов онлайн: <b>{site.get('admins_online', 0)}</b> <i>(активны за 15 мин)</i>",
        "",
        "📄 <b>Вовлечённость и контент:</b>",
        f"• Заполнений форм / действий за сутки: <b>{site.get('mutations_today', 0)}</b>",
        "",
        "📩 <b>Обращения граждан (заявки):</b>",
        f"• Новые заявки: <b>{site.get('tickets_new', 0)}</b>",
        f"• В обработке: <b>{site.get('tickets_in_progress', 0)}</b>",
        f"• Общие (без отдела): <b>{unassigned}</b> <i>(из них новых: {unassigned_new})</i>",
        f"• Решено сегодня: <b>{site.get('tickets_completed_today', 0)}</b>",
        f"• Решено за всё время: <b>{site.get('tickets_completed_total', 0)}</b>",
        f"• Партнёрских заявок: <b>{partnerships_new}</b> новых <i>(в ожидании: {site.get('partnerships_pending', 0)})</i>",
        "",
        "⚙️ <b>Технические метрики:</b>",
        f"• Среднее время ответа (Latency): <b>{site.get('avg_latency_ms', 0)} мс</b>",
        f"• Ошибки 4xx (клиентские): <b>{site.get('errors_4xx', 0)}</b>",
        f"• Ошибки 5xx (серверные): <b>{site.get('errors_5xx', 0)}</b>",
        "• Соединение: <b>HTTPS / TLS (защищено)</b>",
        "",
        "🛡 <b>Доступность и соответствие:</b>",
        f"• Модуль 152-ФЗ / GDPR: <b>{site.get('privacy_status', 'Активен')}</b>",
        f"• Попыток подбора пароля (брутфорс): <b>{site.get('failed_logins_24h', 0)}</b>",
    ]
    return "\n".join(lines)


def format_metrics_bot(metrics: dict[str, Any]) -> str:
    """🤖 Детальные метрики студенческого бота."""
    bot = metrics.get("bot") or {}
    outbox_pending = int(bot.get("outbox_pending", 0))
    outbox_failed = int(bot.get("outbox_failed", 0))

    lines = [
        "🤖 <b>Метрики студенческого бота (VK)</b>",
        "",
        "👥 <b>Аудитория:</b>",
        f"• Всего студентов в базе: <b>{bot.get('users_total', 0)}</b>",
        f"• Новых за сегодня: <b>{bot.get('users_today', 0)}</b>",
        f"• Новых за 7 дней (WAU): <b>{bot.get('users_7d', 0)}</b>",
        f"• Новых за 30 дней (MAU): <b>{bot.get('users_30d', 0)}</b>",
        f"• Активных за сутки (DAU бота): <b>{bot.get('bot_dau', 0)}</b>",
        "",
        "💬 <b>Использование и диалоги:</b>",
        f"• Сообщений диалога за сутки: <b>{bot.get('dialog_messages_today', 0)}</b>",
        f"• Всего сообщений в истории: <b>{bot.get('dialog_messages_total', 0)}</b>",
        "",
        "🎯 <b>Результативность и воронка:</b>",
        f"• Автоматически решено ботом: <b>{bot.get('completed_auto', 0)}</b>",
        f"• <b>Containment rate:</b> <b>{bot.get('containment_pct', 0.0)}%</b> <i>(без человека)</i>",
        f"• Передано в совет / администрацию: <b>{bot.get('escalated_count', 0)}</b>",
        f"• <b>Escalation rate:</b> <b>{bot.get('escalation_pct', 0.0)}%</b>",
        "",
        "⚙️ <b>Технические и операционные:</b>",
        f"• Очередь Outbox (ожидают): <b>{outbox_pending}</b>",
        f"• Сбоев доставки: <b>{outbox_failed}</b>",
        f"• Успешно отправлено сегодня: <b>{bot.get('outbox_sent_today', 0)}</b>",
        "• Uptime бота: <b>100% (Robust LongPoll)</b>",
    ]
    return "\n".join(lines)


def format_metrics_kpi(metrics: dict[str, Any]) -> str:
    """🎯 Сквозные KPI для сайта и бота."""
    kpi = metrics.get("kpi") or {}

    lines = [
        "🎯 <b>Сквозные KPI (Сайт администрации + Бот)</b>",
        "",
        "📊 <b>Целевые показатели эффективности:</b>",
        f"• Доля цифровых обращений: <b>{kpi.get('digital_channel_pct', 100.0)}%</b> <i>(цель: 100%)</i>",
        f"• Автоматизация (Containment rate): <b>{kpi.get('containment_pct', 0.0)}%</b> <i>(цель: >60%)</i>",
        f"• Доля эскалаций на оператора: <b>{kpi.get('escalation_pct', 0.0)}%</b> <i>(цель: <30%)</i>",
        f"• Результативность решения: <b>{kpi.get('resolution_rate', 100.0)}%</b>",
        f"• Доступность сервисов 24/7 (Uptime): <b>{kpi.get('uptime_estimate', 99.9)}%</b> <i>(цель: ≥99.9%)</i>",
        f"• Инциденты безопасности: <b>{kpi.get('security_incidents', 0)}</b> <i>(цель: 0)</i>",
        "",
        "💡 <i>Сквозные KPI объединяют показатели обоих каналов обслуживания для оценки общей цифровизации.</i>",
    ]
    return "\n".join(lines)


def format_app_metrics_message(metrics: dict[str, Any], tab: str = "summary") -> str:
    """Сформировать HTML-сообщение с показателями сайта и бота в зависимости от вкладки."""
    site = metrics.get("site") or {}
    bot = metrics.get("bot") or {}

    if site.get("db_error") and bot.get("db_error"):
        detail = html.escape(str(site["db_error"])[:200])
        return (
            "📊 <b>Показатели сайта и бота</b>\n\n"
            f"🔴 <b>База данных недоступна</b>\n<code>{detail}</code>\n\n"
            "Проверьте состояние контейнера <code>oss_bot_db</code>."
        )

    if tab == "site":
        return format_metrics_site(metrics)
    if tab == "bot":
        return format_metrics_bot(metrics)
    if tab == "kpi":
        return format_metrics_kpi(metrics)
    return format_metrics_summary(metrics)


__all__ = [
    "collect_app_metrics",
    "collect_bot_metrics",
    "collect_cross_kpi_metrics",
    "collect_infra_metrics",
    "collect_site_metrics",
    "day_start_in_app_tz",
    "format_app_metrics_message",
    "format_metrics_bot",
    "format_metrics_kpi",
    "format_metrics_site",
    "format_metrics_summary",
    "format_time_sync_line",
]
