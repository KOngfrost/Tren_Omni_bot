"""
Тесты для новых возможностей Telegram-бота и платформы:
1. Принудительный сброс всех активных сессий (revoke_all_active_sessions).
2. Фильтрация ответов о техработах только по триггерным словам (is_maintenance_trigger).
3. Многоуровневые метрики сайта, бота и сквозные KPI по вкладкам (/metrics).
4. Клавиатуры и хендлеры Telegram-бота для сброса сессий и переключения метрик.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from core.admin_presence import (
    ADMIN_HEARTBEAT_PREFIX,
    _memory_presence,
    revoke_all_active_sessions,
)
from core.maintenance import is_maintenance_trigger


def test_is_maintenance_trigger_words():
    """Проверка определения триггерных слов для ответа о техработах."""
    # Команды и стандартные фразы
    assert is_maintenance_trigger("/start") is True
    assert is_maintenance_trigger("/help") is True
    assert is_maintenance_trigger("!start") is True
    assert is_maintenance_trigger("Привет!") is True
    assert is_maintenance_trigger("Здравствуйте, подскажите") is True
    assert is_maintenance_trigger("хочу подать обращение") is True
    assert is_maintenance_trigger("Где подать заявку?") is True
    assert is_maintenance_trigger("Главное Меню") is True

    # Кнопки с payload
    assert is_maintenance_trigger(None, payload='{"command": "start"}') is True
    assert is_maintenance_trigger("", payload='{"button": "faq"}') is True

    # Случайный текст без триггеров не должен вызывать ответ
    assert is_maintenance_trigger("какая-то случайная бессвязная фраза") is False
    assert is_maintenance_trigger("1234567890") is False
    assert is_maintenance_trigger("") is False
    assert is_maintenance_trigger(None, payload=None) is False


async def test_revoke_all_active_sessions_memory_fallback():
    """Сброс сессий при отсутствии Redis очищает in-memory кэш присутствия."""
    _memory_presence[1] = 999999999.0
    _memory_presence[2] = 999999999.0

    with patch("core.admin_presence.get_redis_client", return_value=None):
        revoked = await revoke_all_active_sessions()
        assert revoked >= 2
        assert len(_memory_presence) == 0


async def test_revoke_all_active_sessions_with_redis():
    """Сброс сессий в Redis находит и удаляет session:* и admin:heartbeat:*."""
    mock_redis = AsyncMock()

    async def mock_scan_iter(match: str, count: int = 100):
        if "session:" in match:
            yield "session:token1"
            yield "session:token2"
        elif ADMIN_HEARTBEAT_PREFIX in match:
            yield f"{ADMIN_HEARTBEAT_PREFIX}admin1"

    mock_redis.scan_iter = mock_scan_iter
    mock_redis.delete = AsyncMock(return_value=1)

    with patch("core.admin_presence.get_redis_client", return_value=mock_redis):
        revoked = await revoke_all_active_sessions()
        assert revoked == 3
        # Проверяем, что mock_redis.delete был вызван
        assert mock_redis.delete.call_count == 2


def test_telegram_keyboards_integration():
    """Проверка наличия новых кнопок в клавиатурах Telegram."""
    from bots.telegram.keyboards import (
        get_kickall_confirmation_keyboard,
        get_main_reply_keyboard,
        get_maintenance_inline_keyboard,
        get_metrics_inline_keyboard,
    )

    # 1. Главное меню
    main_kb = get_main_reply_keyboard()
    main_buttons = [btn.text for row in main_kb.keyboard for btn in row]
    assert "🚪 Сброс сессий" in main_buttons
    assert "📈 Статистика" in main_buttons

    # 2. Меню техработ
    maint_kb = get_maintenance_inline_keyboard(enabled=True)
    maint_cbs = [btn.callback_data for row in maint_kb.inline_keyboard for btn in row]
    assert "kickall:menu" in maint_cbs

    # 3. Меню подтверждения сброса сессий
    kick_kb = get_kickall_confirmation_keyboard()
    kick_cbs = [btn.callback_data for row in kick_kb.inline_keyboard for btn in row]
    assert "kickall:confirm" in kick_cbs
    assert "kickall:cancel" in kick_cbs

    # 4. Меню метрик с вкладками
    metrics_kb = get_metrics_inline_keyboard(tab="summary")
    metrics_cbs = [btn.callback_data for row in metrics_kb.inline_keyboard for btn in row]
    assert "metrics:refresh" in metrics_cbs
    assert "metrics:tab:site" in metrics_cbs
    assert "metrics:tab:bot" in metrics_cbs
    assert "metrics:tab:kpi" in metrics_cbs

    # Текущая выбранная вкладка помечена как status:noop
    assert "status:noop" in metrics_cbs


async def test_metrics_tabs_formatting():
    """Проверка форматирования всех 4 вкладок метрик и валидности HTML."""
    from bots.telegram.app_metrics import format_app_metrics_message

    sample_metrics = {
        "site": {
            "admins_online": 3,
            "requests_24h": 1250,
            "dau": 420,
            "wau": 1850,
            "mau": 6200,
            "errors_4xx": 12,
            "errors_5xx": 1,
            "uptime_pct": 99.98,
            "avg_latency_ms": 45,
            "forms_submitted_24h": 68,
            "failed_logins_24h": 2,
            "tickets_created_today": 15,
            "tickets_completed_today": 12,
            "tickets_completed_total": 450,
            "tickets_unassigned": 2,
        },
        "bot": {
            "users_total": 520,
            "users_today": 25,
            "users_7d": 110,
            "users_30d": 380,
            "bot_dau": 95,
            "dialog_messages_today": 340,
            "dialog_messages_total": 8500,
            "outbox_pending": 0,
            "outbox_failed": 0,
            "outbox_sent_today": 45,
            "all_tickets": 500,
            "completed_auto": 350,
            "containment_pct": 77.8,
            "escalated_count": 55,
            "escalation_pct": 11.0,
        },
        "infra": {
            "time_sync": {"offset_seconds": 0.05, "synchronized": True},
        },
        "kpi": {
            "digital_share_pct": 100.0,
            "containment_pct": 77.8,
            "escalation_pct": 11.0,
            "resolution_pct": 90.0,
            "uptime_pct": 99.98,
            "target_sla_pct": 95.0,
        },
    }

    # Вкладка 1: Сводка
    text_summary = format_app_metrics_message(sample_metrics, tab="summary")
    assert "Показатели сайта и бота" in text_summary
    assert "Сводный дашборд KPI" in text_summary
    assert "Администраторов онлайн:" in text_summary
    assert "3" in text_summary
    assert text_summary.count("<b>") == text_summary.count("</b>")

    # Вкладка 2: Сайт
    text_site = format_app_metrics_message(sample_metrics, tab="site")
    assert "Метрики сайта администрации" in text_site
    assert "DAU" in text_site
    assert "WAU" in text_site
    assert "MAU" in text_site
    assert "152-ФЗ" in text_site
    assert "Ошибки 4xx" in text_site
    assert "Ошибки 5xx" in text_site
    assert text_site.count("<b>") == text_site.count("</b>")

    # Вкладка 3: Бот
    text_bot = format_app_metrics_message(sample_metrics, tab="bot")
    assert "Метрики студенческого бота" in text_bot
    assert "Containment rate" in text_bot
    assert "Escalation rate" in text_bot
    assert "Использование и диалоги" in text_bot
    assert text_bot.count("<b>") == text_bot.count("</b>")

    # Вкладка 4: KPI
    text_kpi = format_app_metrics_message(sample_metrics, tab="kpi")
    assert "Сквозные KPI" in text_kpi
    assert "Containment rate" in text_kpi
    assert "Доля цифровых обращений" in text_kpi
    assert text_kpi.count("<b>") == text_kpi.count("</b>")


async def test_telegram_kickall_handlers():
    """Проверка работы обработчиков /kickall и подтверждения сброса."""
    from bots.telegram.handlers.maintenance import (
        callback_kickall_cancel,
        callback_kickall_confirm,
        cmd_kickall_prompt,
    )

    # 1. Запрос подтверждения через Message
    message_mock = AsyncMock()
    message_mock.answer = AsyncMock()
    await cmd_kickall_prompt(message_mock)
    message_mock.answer.assert_called_once()
    assert "Принудительный сброс активных сессий" in message_mock.answer.call_args[0][0]

    # 2. Подтверждение сброса через CallbackQuery
    callback_confirm = AsyncMock()
    callback_confirm.from_user.id = 12345
    callback_confirm.message = AsyncMock()
    callback_confirm.answer = AsyncMock()

    with patch("core.admin_presence.revoke_all_active_sessions", return_value=5):
        await callback_kickall_confirm(callback_confirm)
        callback_confirm.answer.assert_called_once()
        assert "Сброшено 5 сессий" in callback_confirm.answer.call_args[0][0]
        assert "Все активные сессии сброшены" in callback_confirm.message.edit_text.call_args[0][0]

    # 3. Отмена сброса
    callback_cancel = AsyncMock()
    callback_cancel.message = AsyncMock()
    callback_cancel.answer = AsyncMock()
    await callback_kickall_cancel(callback_cancel)
    callback_cancel.answer.assert_called_once_with("Отменено.")
    assert "Сброс сессий отменён" in callback_cancel.message.edit_text.call_args[0][0]


async def test_telegram_metrics_tab_callback():
    """Проверка переключения вкладок метрик через CallbackQuery."""
    from bots.telegram.handlers.status import callback_metrics_tab

    callback = AsyncMock()
    callback.data = "metrics:tab:site"
    callback.message = AsyncMock()
    callback.answer = AsyncMock()

    with patch(
        "bots.telegram.handlers.status.collect_app_metrics",
        return_value={"site": {}, "bot": {}, "infra": {}, "kpi": {}},
    ):
        await callback_metrics_tab(callback)
        callback.message.edit_text.assert_called_once()
        sent_text = callback.message.edit_text.call_args[0][0]
        assert "Метрики сайта администрации" in sent_text
