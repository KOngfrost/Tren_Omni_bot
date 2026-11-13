"""тесты для сервиса мониторинга yen_watchdog и бота check_me_up."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

from scripts.yen_watchdog import (
    ServerAdminStore,
    WatchdogConfig,
    WatchdogState,
    parse_admin_ids,
    process_bot_message,
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


def test_server_admin_store_add_and_list() -> None:
    """проверка добавления и чтения администраторов в хранилище Check_me_up."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        store = ServerAdminStore(file_path=tmp_path, root_admin_id=6665679072)
        # изначально только root
        assert store.get_admins() == [6665679072]

        # добавляем нового админа
        ok, msg = store.add_admin(
            7328025089, note="второй аккаунт", added_by="telegram:6665679072"
        )
        assert ok is True
        assert 7328025089 in store.get_admins()

        # повторное добавление должно быть отклонено
        ok2, msg2 = store.add_admin(7328025089)
        assert ok2 is False
        assert "уже является" in msg2.lower()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_server_admin_store_del_and_root_protection() -> None:
    """проверка удаления и защиты главного администратора от удаления."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        store = ServerAdminStore(file_path=tmp_path, root_admin_id=6665679072)
        store.add_admin(7328025089)

        # попытка удалить главного админа должна быть отклонена
        ok_root, msg_root = store.del_admin(6665679072, root_id=6665679072)
        assert ok_root is False
        assert "главного администратора" in msg_root.lower()

        # удаление дополнительного админа
        ok_extra, msg_extra = store.del_admin(7328025089, root_id=6665679072)
        assert ok_extra is True
        assert 7328025089 not in store.get_admins()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_process_bot_message_unauthorized_user() -> None:
    """проверка отказа постороннему пользователю."""
    store = ServerAdminStore(file_path="/tmp/test_admins.json", root_admin_id=6665679072)
    cfg = WatchdogConfig()
    state = WatchdogState()

    msg = {
        "from": {"id": 999999999, "first_name": "Stranger"},
        "chat": {"id": 999999999},
        "text": "/start",
    }
    reply = process_bot_message(msg, cfg, store, state)
    assert reply is not None
    assert "доступ запрещен" in reply.lower()


def test_process_bot_message_addadmin_by_root() -> None:
    """проверка выполнения команды /addadmin главным тех-админом."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        store = ServerAdminStore(file_path=tmp_path, root_admin_id=6665679072)
        cfg = WatchdogConfig(admin_chat_id=6665679072)
        state = WatchdogState()

        msg = {
            "from": {"id": 6665679072, "first_name": "Root"},
            "chat": {"id": 6665679072},
            "text": "/addadmin 7328025089 второй_аккаунт",
        }
        with patch("scripts.yen_watchdog.send_telegram_notification") as mock_notify:
            reply = process_bot_message(msg, cfg, store, state)
            assert reply is not None
            assert "успешно добавлен" in reply.lower()
            assert 7328025089 in store.get_admins()
            # новому админу должно быть отправлено приветствие
            mock_notify.assert_called_once()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_process_bot_message_addadmin_by_non_root() -> None:
    """проверка отклонения команды /addadmin от дополнительного администратора."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        store = ServerAdminStore(file_path=tmp_path, root_admin_id=6665679072)
        store.add_admin(7328025089)
        cfg = WatchdogConfig(admin_chat_id=6665679072)
        state = WatchdogState()

        msg = {
            "from": {"id": 7328025089, "first_name": "Extra"},
            "chat": {"id": 7328025089},
            "text": "/addadmin 111222333",
        }
        reply = process_bot_message(msg, cfg, store, state)
        assert reply is not None
        assert "доступ запрещен" in reply.lower()
        assert 111222333 not in store.get_admins()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_process_bot_message_admins_list() -> None:
    """проверка вывода списка администраторов по команде /admins."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        store = ServerAdminStore(file_path=tmp_path, root_admin_id=6665679072)
        store.add_admin(7328025089, note="второй аккаунт")
        cfg = WatchdogConfig(admin_chat_id=6665679072)
        state = WatchdogState()

        msg = {
            "from": {"id": 6665679072, "first_name": "Root"},
            "chat": {"id": 6665679072},
            "text": "/admins",
        }
        reply = process_bot_message(msg, cfg, store, state)
        assert reply is not None
        assert "6665679072" in reply
        assert "7328025089" in reply
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


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
