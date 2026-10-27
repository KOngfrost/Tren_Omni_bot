"""Тесты меню Технического администратора в VK-боте (bots/vk/handlers/tech_admin.py)."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bots.telegram.docker_client import ContainerInfo
from bots.vk.common import _main_keyboard_for
from bots.vk.handlers.tech_admin import (
    is_tech_admin,
    tech_2fa_handler,
    tech_action_dynamic_handler,
    tech_backup_handler,
    tech_containers_handler,
    tech_kickall_handler,
    tech_maintenance_handler,
    tech_maintenance_toggle_handler,
    tech_metrics_handler,
    tech_panel_handler,
    tech_return_main_menu,
    tech_status_handler,
)
from bots.vk.keyboards import (
    build_main_keyboard,
    build_tech_admin_keyboard,
    build_tech_containers_keyboard,
    build_tech_logs_keyboard,
    build_tech_maintenance_keyboard,
)
from core.backup_service import (
    compute_sha256,
    ensure_backup_dir,
    perform_isolated_database_backup,
    rotate_old_backups,
    save_backup_file,
)
from core.bot_core import BotCore
from core.config import settings
from core.models import User

TECH_ADMIN_ID = 777000111
OTHER_USER_ID = 100000001


@pytest.fixture(autouse=True)
def set_tech_admin_env(monkeypatch):
    """Фикстура, устанавливающая тестовый TECH_ADMIN_VK_ID."""
    monkeypatch.setattr(settings, "TECH_ADMIN_VK_ID", TECH_ADMIN_ID)


# === 1. Тесты прав доступа и BotCore ===


def test_bot_core_is_tech_admin():
    """Проверка определения ID технического администратора."""
    assert BotCore.is_tech_admin_vk_id(TECH_ADMIN_ID) is True
    assert BotCore.is_tech_admin_vk_id(OTHER_USER_ID) is False
    assert BotCore.is_tech_admin_vk_id(None) is False
    assert is_tech_admin(TECH_ADMIN_ID) is True
    assert is_tech_admin(OTHER_USER_ID) is False


def test_tech_admin_disabled_when_zero(monkeypatch):
    """При значении 0 доступ к меню тех-админа отключен для всех."""
    monkeypatch.setattr(settings, "TECH_ADMIN_VK_ID", 0)
    assert BotCore.is_tech_admin_vk_id(TECH_ADMIN_ID) is False
    assert BotCore.is_tech_admin_vk_id(0) is False


# === 2. Тесты генерации клавиатур ===


def test_build_main_keyboard_with_tech_admin():
    """Кнопка '🔧 Тех-админ' отображается только при is_tech_admin=True."""
    kb_tech = json.loads(build_main_keyboard(is_admin=False, is_tech_admin=True))
    all_labels = [btn["action"]["label"] for row in kb_tech["buttons"] for btn in row]
    assert "🔧 Тех-админ" in all_labels
    assert "Админ" not in all_labels

    kb_both = json.loads(build_main_keyboard(is_admin=True, is_tech_admin=True))
    labels_both = [btn["action"]["label"] for row in kb_both["buttons"] for btn in row]
    assert "🔧 Тех-админ" in labels_both
    assert "Админ" in labels_both

    kb_student = json.loads(build_main_keyboard(is_admin=False, is_tech_admin=False))
    labels_student = [btn["action"]["label"] for row in kb_student["buttons"] for btn in row]
    assert "🔧 Тех-админ" not in labels_student
    assert "Админ" not in labels_student


def test_build_tech_admin_keyboards():
    """Проверка структуры клавиатур панели технического администратора."""
    kb_admin = json.loads(build_tech_admin_keyboard())
    admin_labels = [btn["action"]["label"] for row in kb_admin["buttons"] for btn in row]
    assert "📊 Сервер" in admin_labels
    assert "📈 Метрики" in admin_labels
    assert "💾 Бэкап БД" in admin_labels
    assert "🚧 Техработы" in admin_labels
    assert "🔄 Контейнеры" in admin_labels
    assert "🚪 Сброс сессий" in admin_labels
    assert "Главное меню" in admin_labels

    kb_containers = json.loads(build_tech_containers_keyboard(["app", "web"]))
    c_labels = [btn["action"]["label"] for row in kb_containers["buttons"] for btn in row]
    assert "🔄 Перезапуск app" in c_labels
    assert "🔄 Перезапуск web" in c_labels

    kb_maint_off = json.loads(build_tech_maintenance_keyboard(is_active=False))
    m_labels_off = [btn["action"]["label"] for row in kb_maint_off["buttons"] for btn in row]
    assert "🔴 Включить техработы" in m_labels_off

    kb_maint_on = json.loads(build_tech_maintenance_keyboard(is_active=True))
    m_labels_on = [btn["action"]["label"] for row in kb_maint_on["buttons"] for btn in row]
    assert "🟢 Отключить техработы" in m_labels_on

    kb_logs = json.loads(build_tech_logs_keyboard(["app", "db"]))
    l_labels = [btn["action"]["label"] for row in kb_logs["buttons"] for btn in row]
    assert "📋 Логи app" in l_labels


@pytest.mark.asyncio
async def test_main_keyboard_for_passes_tech_admin(db_session_maker):
    """_main_keyboard_for выдает тех-кнопку для тех-админа."""
    async with db_session_maker() as session:
        session.add(User(vk_id=TECH_ADMIN_ID, full_name="Tech Admin"))
        session.add(User(vk_id=OTHER_USER_ID, full_name="Student"))
        await session.commit()

    kb_tech = json.loads(await _main_keyboard_for(TECH_ADMIN_ID))
    labels_tech = [btn["action"]["label"] for row in kb_tech["buttons"] for btn in row]
    assert "🔧 Тех-админ" in labels_tech

    kb_student = json.loads(await _main_keyboard_for(OTHER_USER_ID))
    labels_student = [btn["action"]["label"] for row in kb_student["buttons"] for btn in row]
    assert "🔧 Тех-админ" not in labels_student


# === 3. Тесты обработчиков панели тех-админа ===


@pytest.mark.asyncio
async def test_tech_panel_access_denied(db_session_maker):
    """Обычный пользователь получает отказ в доступе при попытке входа в тех-панель."""
    msg = AsyncMock()
    msg.from_id = OTHER_USER_ID
    msg.text = "🔧 тех-админ"
    msg.answer = AsyncMock()

    await tech_panel_handler(msg)

    msg.answer.assert_called_once()
    assert "⛔ Доступ запрещён" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_tech_panel_access_granted():
    """Технический администратор успешно получает доступ к панели."""
    msg = AsyncMock()
    msg.from_id = TECH_ADMIN_ID
    msg.text = "🔧 тех-админ"
    msg.answer = AsyncMock()

    await tech_panel_handler(msg)

    msg.answer.assert_called_once()
    reply = msg.answer.call_args[0][0]
    assert "Панель Технического Администратора" in reply
    assert str(TECH_ADMIN_ID) in reply


@pytest.mark.asyncio
async def test_tech_status_handler():
    """Проверка вывода метрик хоста и статуса контейнеров."""
    msg = AsyncMock()
    msg.from_id = TECH_ADMIN_ID
    msg.answer = AsyncMock()

    fake_containers = [
        ContainerInfo(
            id="c1", name="oss_bot_app", image="img1", state="running", status="Up 2 hours"
        ),
        ContainerInfo(
            id="c2", name="oss_bot_db", image="img2", state="running", status="Up 2 hours"
        ),
    ]

    with patch(
        "bots.vk.handlers.tech_admin.DockerClient.list_containers",
        new=AsyncMock(return_value=fake_containers),
    ):
        await tech_status_handler(msg)

    msg.answer.assert_called_once()
    reply = msg.answer.call_args[0][0]
    assert "Состояние сервера и инфраструктуры" in reply
    assert "CPU:" in reply
    assert "RAM:" in reply
    assert "oss_bot_app" in reply


@pytest.mark.asyncio
async def test_tech_metrics_handler():
    """Проверка сбора метрик тикетов и очередей."""
    msg = AsyncMock()
    msg.from_id = TECH_ADMIN_ID
    msg.answer = AsyncMock()

    await tech_metrics_handler(msg)

    msg.answer.assert_called_once()
    reply = msg.answer.call_args[0][0]
    assert "Показатели сервиса" in reply
    assert "PostgreSQL:" in reply
    assert "Обращения студентов:" in reply
    assert "Очередь Outbox:" in reply


@pytest.mark.asyncio
async def test_tech_backup_handler(tmp_path):
    """Проверка запуска изолированного бэкапа."""
    msg = AsyncMock()
    msg.from_id = TECH_ADMIN_ID
    msg.answer = AsyncMock()

    fake_result = (True, b"fake compressed data", "oss_bot_backup_test.sql.gz", "fake_sha256_hash")
    with patch(
        "bots.vk.handlers.tech_admin.perform_isolated_database_backup",
        new=AsyncMock(return_value=fake_result),
    ):
        await tech_backup_handler(msg)

    assert msg.answer.call_count == 2
    reply = msg.answer.call_args_list[1][0][0]
    assert "Резервная копия базы данных успешно создана" in reply
    assert "oss_bot_backup_test.sql.gz" in reply
    assert "fake_sha256_hash" in reply


@pytest.mark.asyncio
async def test_tech_maintenance_handler_and_toggle():
    """Проверка просмотра и переключения режима техработ."""
    msg = AsyncMock()
    msg.from_id = TECH_ADMIN_ID
    msg.answer = AsyncMock()

    # 1. Просмотр текущего статуса
    await tech_maintenance_handler(msg)
    reply1 = msg.answer.call_args[0][0]
    assert "Режим технического обслуживания" in reply1

    # 2. Переключение
    await tech_maintenance_toggle_handler(msg)
    reply2 = msg.answer.call_args[0][0]
    assert "Режим технических работ успешно" in reply2


@pytest.mark.asyncio
async def test_tech_containers_and_restart():
    """Проверка списка контейнеров и команды перезапуска."""
    msg = AsyncMock()
    msg.from_id = TECH_ADMIN_ID
    msg.answer = AsyncMock()

    # 1. Список контейнеров
    await tech_containers_handler(msg)
    assert "Управление контейнерами" in msg.answer.call_args[0][0]

    # 2. Перезапуск контейнера
    msg_restart = AsyncMock()
    msg_restart.from_id = TECH_ADMIN_ID
    msg_restart.text = "🔄 Перезапуск oss_bot_web"
    msg_restart.answer = AsyncMock()

    with patch(
        "bots.vk.handlers.tech_admin.DockerClient.restart_container",
        new=AsyncMock(return_value=(True, "Success")),
    ):
        await tech_action_dynamic_handler(msg_restart)

    assert msg_restart.answer.call_count == 2
    assert "успешно перезапущен" in msg_restart.answer.call_args_list[1][0][0]


@pytest.mark.asyncio
async def test_tech_logs_action():
    """Проверка просмотра логов выбранного контейнера."""
    msg = AsyncMock()
    msg.from_id = TECH_ADMIN_ID
    msg.text = "📋 Логи oss_bot_app"
    msg.answer = AsyncMock()

    with patch(
        "bots.vk.handlers.tech_admin.DockerClient.get_container_logs",
        new=AsyncMock(return_value="[INFO] Bot started"),
    ):
        await tech_action_dynamic_handler(msg)

    msg.answer.assert_called_once()
    assert "Последние логи oss_bot_app" in msg.answer.call_args[0][0]
    assert "[INFO] Bot started" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_tech_kickall_handler():
    """Проверка экстренного сброса сессий."""
    msg = AsyncMock()
    msg.from_id = TECH_ADMIN_ID
    msg.answer = AsyncMock()

    mock_redis = MagicMock()

    async def fake_scan(match):
        yield "session:token1"
        yield "session:token2"

    mock_redis.scan_iter = fake_scan
    mock_redis.delete = AsyncMock()

    with patch(
        "bots.vk.handlers.tech_admin.get_redis_client", new=AsyncMock(return_value=mock_redis)
    ):
        await tech_kickall_handler(msg)

    msg.answer.assert_called_once()
    reply = msg.answer.call_args[0][0]
    assert "Сброс активных сессий завершён" in reply
    assert "2" in reply
    mock_redis.delete.assert_called_once_with("session:token1", "session:token2")


@pytest.mark.asyncio
async def test_tech_2fa_and_return_menu(db_session_maker):
    """Проверка статуса 2FA и возврата в главное меню."""
    msg = AsyncMock()
    msg.from_id = TECH_ADMIN_ID
    msg.answer = AsyncMock()

    await tech_2fa_handler(msg)
    assert "Двухфакторная аутентификация" in msg.answer.call_args[0][0]

    await tech_return_main_menu(msg)
    assert "Вы вернулись в главное меню" in msg.answer.call_args[0][0]


# === 4. Тесты core/backup_service.py ===


def test_backup_service_helpers(tmp_path):
    """Проверка сохранения файлов, хеширования и ротации."""
    test_dir = str(tmp_path / "backups")
    ensure_backup_dir(test_dir)
    assert os.path.exists(test_dir)

    data = b"sample backup data"
    path = save_backup_file(data, "oss_bot_backup_test.sql.gz", backup_dir=test_dir)
    assert os.path.exists(path)

    digest = compute_sha256(data)
    assert len(digest) == 64

    # Ротация не удаляет свежий файл
    deleted = rotate_old_backups(test_dir, max_days=7)
    assert deleted == 0


@pytest.mark.asyncio
async def test_perform_isolated_database_backup_meta(tmp_path):
    """Проверка фоллбэка perform_isolated_database_backup в тестовом окружении."""
    test_dir = str(tmp_path / "backups")
    ok, data, filename, digest = await perform_isolated_database_backup(backup_dir=test_dir)
    assert ok is True
    assert data is not None
    assert "oss_bot_backup_" in filename
    assert digest is not None
    assert len(digest) == 64
