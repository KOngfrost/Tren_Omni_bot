"""Комплексные тесты ВСЕХ кнопок и элементов управления в Telegram-боте мониторинга.

Проверяет:
1. Все кнопки главного Reply-меню (Статус, Перезапуск, Логи, Бэкап, Техработы, 2FA, Помощь).
2. Все инлайн-кнопки карточки статуса (Обновить, Перезапуск, Логи, Бэкап БД, Техработы, 2FA).
3. Инлайн-кнопки управления режимом техработ (Включить, Отключить, Обновить).
4. Инлайн-кнопки управления 2FA (Включить, Отключить, Обновить).
5. Инлайн-кнопки подтверждения и отмены перезапуска сервисов.
6. Инлайн-кнопки карточки бэкапа и скачивания.
7. Инлайн-кнопки панели и запуска Mini App.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User

from bots.telegram.docker_client import ContainerInfo, DockerClient
from bots.telegram.handlers.backup import cmd_backup
from bots.telegram.handlers.base import cmd_help, cmd_start
from bots.telegram.handlers.containers import (
    callback_reboot_cancel,
    callback_reboot_confirm,
    callback_restart_all,
    callback_restart_single,
    callback_view_logs,
    cmd_logs_menu,
    cmd_reboot,
    cmd_restart_menu,
)
from bots.telegram.handlers.maintenance import (
    callback_maintenance_disable,
    callback_maintenance_enable,
    callback_maintenance_refresh,
    cmd_maintenance,
)
from bots.telegram.handlers.status import callback_status_refresh, cmd_status
from bots.telegram.handlers.two_factor import (
    callback_two_factor_disable,
    callback_two_factor_enable,
    callback_two_factor_refresh,
    cmd_two_factor,
)
from bots.telegram.keyboards import (
    get_maintenance_inline_keyboard,
    get_main_reply_keyboard,
    get_panel_inline_keyboard,
    get_reboot_confirmation_keyboard,
    get_status_inline_keyboard,
    get_two_factor_inline_keyboard,
)


def _make_msg(text: str, user_id: int = 12345) -> Message:
    user = User(id=user_id, is_bot=False, first_name="Admin", username="admin")
    chat = Chat(id=user_id, type="private")
    msg = MagicMock(spec=Message)
    msg.from_user = user
    msg.chat = chat
    msg.text = text
    sub_msg = MagicMock()
    sub_msg.edit_text = AsyncMock()
    sub_msg.delete = AsyncMock()
    msg.answer = AsyncMock(return_value=sub_msg)
    msg.edit_text = AsyncMock()
    msg.delete = AsyncMock()
    msg.answer_document = AsyncMock()
    msg.reply_document = AsyncMock()
    return msg


def _make_cb(data: str, user_id: int = 12345) -> CallbackQuery:
    user = User(id=user_id, is_bot=False, first_name="Admin", username="admin")
    msg = _make_msg("orig", user_id)
    cb = MagicMock(spec=CallbackQuery)
    cb.from_user = user
    cb.data = data
    cb.message = msg
    cb.answer = AsyncMock()
    return cb


# =========================================================================
# 1. Тестирование генерации и содержимого всех клавиатур
# =========================================================================


def test_main_reply_keyboard_buttons():
    kb = get_main_reply_keyboard(webapp_url="https://example.com/panel")
    all_texts = [btn.text for row in kb.keyboard for btn in row]
    assert "📱 Веб-панель" in all_texts
    assert "📊 Статус" in all_texts
    assert "🔄 Перезапуск" in all_texts
    assert "📋 Логи" in all_texts
    assert "💾 Бэкап" in all_texts
    assert "🚧 Техработы" in all_texts
    assert "🔐 2FA" in all_texts
    assert "ℹ️ Помощь" in all_texts


def test_status_inline_keyboard_buttons():
    kb = get_status_inline_keyboard(webapp_url="https://example.com/panel")
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
    assert "status:refresh" in callbacks
    assert "menu:restart" in callbacks
    assert "menu:logs" in callbacks
    assert "backup:create" in callbacks
    assert "maint:menu" in callbacks
    assert "2fa:menu" in callbacks


def test_maintenance_inline_keyboard_toggle():
    kb_disabled = get_maintenance_inline_keyboard(enabled=False)
    cb_disabled = [btn.callback_data for row in kb_disabled.inline_keyboard for btn in row]
    assert "maint:enable" in cb_disabled
    assert "maint:refresh" in cb_disabled
    assert "status:refresh" in cb_disabled

    kb_enabled = get_maintenance_inline_keyboard(enabled=True)
    cb_enabled = [btn.callback_data for row in kb_enabled.inline_keyboard for btn in row]
    assert "maint:disable" in cb_enabled


def test_two_factor_inline_keyboard_toggle():
    kb_disabled = get_two_factor_inline_keyboard(enabled=False)
    cb_disabled = [btn.callback_data for row in kb_disabled.inline_keyboard for btn in row]
    assert "2fa:enable" in cb_disabled

    kb_enabled = get_two_factor_inline_keyboard(enabled=True)
    cb_enabled = [btn.callback_data for row in kb_enabled.inline_keyboard for btn in row]
    assert "2fa:disable" in cb_enabled


def test_reboot_confirmation_keyboard():
    kb = get_reboot_confirmation_keyboard()
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "reboot:confirm" in callbacks
    assert "reboot:cancel" in callbacks


def test_panel_inline_keyboard():
    kb = get_panel_inline_keyboard(webapp_url="https://example.com/panel")
    urls = [btn.url for row in kb.inline_keyboard for btn in row if btn.url]
    assert "https://example.com/panel" in urls
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
    assert "status:refresh" in callbacks


# =========================================================================
# 2. Тестирование обработчиков Reply-кнопок
# =========================================================================


@pytest.mark.asyncio
async def test_button_start():
    from core.config import get_settings
    msg = _make_msg("/start")
    await cmd_start(msg, get_settings())
    assert msg.answer.call_count >= 1
    assert "Панель управления" in msg.answer.call_args_list[0][0][0]


@pytest.mark.asyncio
async def test_button_help():
    from core.config import get_settings
    msg = _make_msg("ℹ️ Помощь")
    await cmd_help(msg, get_settings())
    msg.answer.assert_called_once()
    assert "Справочник команд" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_button_status():
    msg = _make_msg("📊 Статус")
    docker_mock = AsyncMock(spec=DockerClient)
    docker_mock.list_containers.return_value = [
        ContainerInfo("1", "oss_bot_app", "img", "running", "Up", "healthy", True)
    ]
    await cmd_status(msg, docker_mock)
    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0]
    assert "Контейнеры Docker" in text


@pytest.mark.asyncio
async def test_button_maintenance_menu():
    msg = _make_msg("🚧 Техработы")
    with patch("bots.telegram.handlers.maintenance.get_maintenance_info", return_value={"enabled": False, "message": "off"}):
        await cmd_maintenance(msg)
    msg.answer.assert_called_once()
    assert "Управление режимом технических работ" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_button_two_factor_menu():
    msg = _make_msg("🔐 2FA")
    with patch("bots.telegram.handlers.two_factor.get_two_factor_info", return_value={"enabled": True, "source": "db"}):
        await cmd_two_factor(msg)
    msg.answer.assert_called_once()
    assert "Управление двухфакторной аутентификацией" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_button_backup_menu():
    from core.config import get_settings
    msg = _make_msg("💾 Бэкап")
    docker_mock = AsyncMock(spec=DockerClient)
    settings = get_settings()
    with patch("bots.telegram.handlers.backup.perform_database_backup", new=AsyncMock(return_value=(True, b"backup", "backup.sql.gz"))):
        await cmd_backup(msg, docker_mock, settings)
    msg.answer.assert_called_once()


# =========================================================================
# 3. Тестирование инлайн-кнопок (Callbacks)
# =========================================================================


@pytest.mark.asyncio
async def test_inline_button_status_refresh():
    cb = _make_cb("status:refresh")
    docker_mock = AsyncMock(spec=DockerClient)
    docker_mock.list_containers.return_value = []
    await callback_status_refresh(cb, docker_mock)
    cb.answer.assert_called_once()
    cb.message.edit_text.assert_called_once()


@pytest.mark.asyncio
async def test_inline_button_restart_menu():
    cb = _make_cb("menu:restart")
    docker_mock = AsyncMock(spec=DockerClient)
    docker_mock.list_containers.return_value = []
    await cmd_restart_menu(cb, docker_mock)
    cb.answer.assert_called_once()
    cb.message.edit_text.assert_called_once()
    assert "Выберите контейнер для перезапуска" in cb.message.edit_text.call_args[0][0]


@pytest.mark.asyncio
async def test_inline_button_reboot_cancel():
    cb = _make_cb("reboot:cancel")
    await callback_reboot_cancel(cb)
    cb.answer.assert_called_once_with("Отменено.")
    cb.message.edit_text.assert_called_once()


@pytest.mark.asyncio
async def test_inline_button_reboot_confirm():
    cb = _make_cb("reboot:confirm")
    docker_mock = AsyncMock(spec=DockerClient)
    docker_mock.restart_container = AsyncMock(return_value=True)
    docker_mock.list_containers.return_value = [
        ContainerInfo("1", "oss_bot_app", "img", "running", "Up", "healthy", True)
    ]
    await callback_reboot_confirm(cb, docker_mock)
    cb.answer.assert_called_once_with("Перезапуск запущен!")
    assert cb.message.edit_text.call_count >= 1


@pytest.mark.asyncio
async def test_inline_button_logs_menu():
    cb = _make_cb("menu:logs")
    docker_mock = AsyncMock(spec=DockerClient)
    docker_mock.list_containers.return_value = [
        ContainerInfo("1", "oss_bot_app", "img", "running", "Up", "healthy", True)
    ]
    await cmd_logs_menu(cb, docker_mock)
    cb.answer.assert_called_once()
    cb.message.edit_text.assert_called_once()
    assert "Выберите контейнер для просмотра логов" in cb.message.edit_text.call_args[0][0]


@pytest.mark.asyncio
async def test_inline_button_view_logs():
    cb = _make_cb("logs:oss_bot_app")
    docker_mock = AsyncMock(spec=DockerClient)
    docker_mock.get_container_logs.return_value = "Sample container log line 1\nSample container log line 2"
    await callback_view_logs(cb, docker_mock)
    cb.answer.assert_called_once_with("Загрузка логов...")
    cb.message.edit_text.assert_called_once()
    assert "Последние логи" in cb.message.edit_text.call_args[0][0]


@pytest.mark.asyncio
async def test_inline_button_maint_enable_and_disable():
    cb_en = _make_cb("maint:enable")
    with patch("bots.telegram.handlers.maintenance.set_maintenance_mode", new=AsyncMock(return_value=True)):
        await callback_maintenance_enable(cb_en)
    cb_en.answer.assert_called_once_with("🚨 Режим техработ ВКЛЮЧЕН!", show_alert=True)

    cb_dis = _make_cb("maint:disable")
    with patch("bots.telegram.handlers.maintenance.set_maintenance_mode", new=AsyncMock(return_value=True)):
        await callback_maintenance_disable(cb_dis)
    cb_dis.answer.assert_called_once_with("✅ Режим техработ ВЫКЛЮЧЕН! Системы работают штатно.", show_alert=True)


@pytest.mark.asyncio
async def test_inline_button_two_factor_enable_and_disable():
    cb_en = _make_cb("2fa:enable")
    with patch("bots.telegram.handlers.two_factor.set_two_factor_mode", new=AsyncMock(return_value=True)):
        await callback_two_factor_enable(cb_en)
    cb_en.answer.assert_called_once_with("🔒 Двухфакторная аутентификация (2FA) ВКЛЮЧЕНА!", show_alert=True)

    cb_dis = _make_cb("2fa:disable")
    with patch("bots.telegram.handlers.two_factor.set_two_factor_mode", new=AsyncMock(return_value=True)):
        await callback_two_factor_disable(cb_dis)
    cb_dis.answer.assert_called_once_with("🔓 Двухфакторная аутентификация (2FA) ОТКЛЮЧЕНА! Вход доступен по логину и паролю.", show_alert=True)


@pytest.mark.asyncio
async def test_inline_button_backup_create():
    import gzip
    from core.config import get_settings
    cb = _make_cb("backup:create")
    docker_mock = AsyncMock(spec=DockerClient)
    settings = get_settings()
    sql_data = b"CREATE TABLE test;"
    gz_data = gzip.compress(sql_data)
    with patch("bots.telegram.handlers.backup.perform_database_backup", new=AsyncMock(return_value=(True, gz_data, "backup.sql.gz"))):
        await cmd_backup(cb, docker_mock, settings)
    cb.answer.assert_called_once()
