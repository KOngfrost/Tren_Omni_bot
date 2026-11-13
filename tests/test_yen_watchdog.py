"""тесты для сервиса мониторинга yen_watchdog."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from scripts.yen_watchdog import (
    WatchdogConfig,
    WatchdogState,
    parse_admin_ids,
    process_check_result,
    send_telegram_notification,
)


def test_parse_admin_ids_valid() -> None:
    """проверка разбора списка id администраторов."""
    ids = parse_admin_ids("6665679072, 123456, 7890")
    assert ids == [6665679072, 123456, 7890]


def test_parse_admin_ids_empty() -> None:
    """проверка разбора пустого списка id администраторов."""
    ids = parse_admin_ids("")
    assert ids == []


def test_parse_admin_ids_invalid() -> None:
    """проверка игнорирования нечисловых значений в списке."""
    ids = parse_admin_ids("6665679072, invalid, 12345")
    assert ids == [6665679072, 12345]


def test_state_cycle_initial_success() -> None:
    """проверка успешного первого цикла (штатный режим, 15 мин)."""
    cfg = WatchdogConfig()
    state = WatchdogState()

    new_state, sleep_sec = process_check_result(
        state=state,
        config=cfg,
        is_ok=True,
        category="none",
        details="all ok",
        notifier=MagicMock(),
        maintenance_setter=MagicMock(),
    )

    assert sleep_sec == 900
    assert new_state.consecutive_failures == 0
    assert new_state.maintenance_active is False


def test_state_cycle_first_server_failure() -> None:
    """проверка первого сбоя сервера: 5 минут, без включения техработ, алерт в server bot."""
    cfg = WatchdogConfig()
    state = WatchdogState()
    notifier = MagicMock()
    maint_setter = MagicMock()

    new_state, sleep_sec = process_check_result(
        state=state,
        config=cfg,
        is_ok=False,
        category="server",
        details="ssh timeout",
        notifier=notifier,
        maintenance_setter=maint_setter,
    )

    assert sleep_sec == 300
    assert new_state.consecutive_failures == 1
    assert new_state.maintenance_active is False
    assert new_state.last_failure_category == "server"
    maint_setter.assert_not_called()
    assert notifier.call_count == 1
    # проверяем отправку в server bot
    call_args = notifier.call_args[0]
    assert call_args[0] == cfg.bot_token_server
    assert "проверьте пк" in call_args[2].lower() or "проверте пк" in call_args[2].lower()


def test_state_cycle_first_container_failure() -> None:
    """проверка первого сбоя контейнеров: 5 минут, без включения техработ, алерт в brain bot."""
    cfg = WatchdogConfig()
    state = WatchdogState()
    notifier = MagicMock()
    maint_setter = MagicMock()

    new_state, sleep_sec = process_check_result(
        state=state,
        config=cfg,
        is_ok=False,
        category="container",
        details="oss_bot_web unhealthy",
        notifier=notifier,
        maintenance_setter=maint_setter,
    )

    assert sleep_sec == 300
    assert new_state.consecutive_failures == 1
    assert new_state.maintenance_active is False
    assert new_state.last_failure_category == "container"
    maint_setter.assert_not_called()
    assert notifier.call_count == 1
    call_args = notifier.call_args[0]
    assert call_args[0] == cfg.bot_token_brain
    assert "oss_bot_web" in call_args[2]


def test_state_cycle_second_container_failure_triggers_maintenance() -> None:
    """проверка второго сбоя: включение режима техработ и уведомление."""
    cfg = WatchdogConfig()
    state = WatchdogState(consecutive_failures=1, last_failure_category="container")
    notifier = MagicMock()
    maint_setter = MagicMock()

    new_state, sleep_sec = process_check_result(
        state=state,
        config=cfg,
        is_ok=False,
        category="container",
        details="oss_bot_web unhealthy",
        notifier=notifier,
        maintenance_setter=maint_setter,
    )

    assert sleep_sec == 300
    assert new_state.consecutive_failures == 2
    assert new_state.maintenance_active is True
    maint_setter.assert_called_once_with(True)
    assert notifier.call_count == 1
    call_args = notifier.call_args[0]
    assert call_args[0] == cfg.bot_token_brain
    assert "активирован" in call_args[2].lower() or "включен" in call_args[2].lower()


def test_state_cycle_third_failure_persists_maintenance() -> None:
    """проверка последующих сбоев (3+): сохранение режима техработ и интервала 5 минут."""
    cfg = WatchdogConfig()
    state = WatchdogState(
        consecutive_failures=2,
        maintenance_active=True,
        last_failure_category="container",
    )
    notifier = MagicMock()
    maint_setter = MagicMock()

    new_state, sleep_sec = process_check_result(
        state=state,
        config=cfg,
        is_ok=False,
        category="container",
        details="oss_bot_web still down",
        notifier=notifier,
        maintenance_setter=maint_setter,
    )

    assert sleep_sec == 300
    assert new_state.consecutive_failures == 3
    assert new_state.maintenance_active is True
    # повторно не вызываем включение техработ
    maint_setter.assert_not_called()


def test_state_cycle_recovery_disables_maintenance() -> None:
    """проверка восстановления: автоматическое снятие техработ, возврат к 15 минутам."""
    cfg = WatchdogConfig()
    state = WatchdogState(
        consecutive_failures=2,
        maintenance_active=True,
        last_failure_category="container",
    )
    notifier = MagicMock()
    maint_setter = MagicMock()

    new_state, sleep_sec = process_check_result(
        state=state,
        config=cfg,
        is_ok=True,
        category="none",
        details="all healthy",
        notifier=notifier,
        maintenance_setter=maint_setter,
    )

    assert sleep_sec == 900
    assert new_state.consecutive_failures == 0
    assert new_state.maintenance_active is False
    maint_setter.assert_called_once_with(False)
    assert notifier.call_count == 1
    call_args = notifier.call_args[0]
    assert call_args[0] == cfg.bot_token_brain
    assert "восстановлен" in call_args[2].lower() or "снят" in call_args[2].lower()


def test_state_cycle_server_recovery_notifies_server_bot() -> None:
    """проверка восстановления после падения сервера: уведомление отправляется в server bot."""
    cfg = WatchdogConfig()
    state = WatchdogState(
        consecutive_failures=2,
        maintenance_active=True,
        last_failure_category="server",
    )
    notifier = MagicMock()
    maint_setter = MagicMock()

    new_state, sleep_sec = process_check_result(
        state=state,
        config=cfg,
        is_ok=True,
        category="none",
        details="server and all containers healthy",
        notifier=notifier,
        maintenance_setter=maint_setter,
    )

    assert sleep_sec == 900
    assert new_state.consecutive_failures == 0
    assert new_state.maintenance_active is False
    assert notifier.call_count == 1
    call_args = notifier.call_args[0]
    assert call_args[0] == cfg.bot_token_server
    assert "восстановлен" in call_args[2].lower() or "в сети" in call_args[2].lower()


def test_send_telegram_notification_success() -> None:
    """проверка успешной отправки уведомления в telegram."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("requests.post", return_value=mock_resp) as mock_post:
        ok = send_telegram_notification("token123", [6665679072], "test message")
        assert ok is True
        assert mock_post.call_count == 1


def test_send_telegram_notification_network_error() -> None:
    """проверка подавления исключений при сбое сети telegram."""
    with patch("requests.post", side_effect=Exception("network down")):
        ok = send_telegram_notification("token123", [6665679072], "test message")
        assert ok is False
