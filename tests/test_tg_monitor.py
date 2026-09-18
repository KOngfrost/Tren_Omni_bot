"""Тесты для Telegram-бота мониторинга сервера и управления контейнерами."""

import unittest.mock as mock

import pytest
from aiogram.types import Message, User

from bots.telegram.bot import AdminAccessMiddleware
from bots.telegram.docker_client import ContainerInfo, DockerClient
from bots.telegram.monitor_service import MonitorService
from bots.telegram.system_metrics import (
    format_metrics_message,
    get_system_metrics,
    render_progress_bar,
)


def test_render_progress_bar():
    assert render_progress_bar(0, length=10) == "[░░░░░░░░░░]"
    assert render_progress_bar(50, length=10) == "[█████░░░░░]"
    assert render_progress_bar(100, length=10) == "[██████████]"
    assert render_progress_bar(-10, length=10) == "[░░░░░░░░░░]"
    assert render_progress_bar(150, length=10) == "[██████████]"


def test_get_system_metrics():
    metrics = get_system_metrics()
    assert "cpu_percent" in metrics
    assert "cpu_count" in metrics
    assert "ram_total" in metrics
    assert "ram_used" in metrics
    assert "ram_percent" in metrics
    assert "disk_total" in metrics
    assert "disk_used" in metrics
    assert "disk_percent" in metrics
    assert "uptime_str" in metrics

    assert isinstance(metrics["cpu_percent"], int | float)
    assert isinstance(metrics["ram_percent"], int | float)
    assert isinstance(metrics["disk_percent"], int | float)

    text = format_metrics_message(metrics)
    assert "Состояние сервера" in text
    assert "CPU" in text
    assert "RAM" in text
    assert "Диск" in text


def test_container_info():
    c_healthy = ContainerInfo("123", "oss_bot_web", "img", "running", "Up 1 hour (healthy)", "healthy", True)
    assert c_healthy.is_running is True
    assert c_healthy.is_healthy is True
    assert c_healthy.status_emoji == "🟢"
    assert c_healthy.is_project_container is True

    c_unhealthy = ContainerInfo("456", "oss_bot_app", "img", "running", "Up 1 hour (unhealthy)", "unhealthy", True)
    assert c_unhealthy.is_healthy is False
    assert c_unhealthy.status_emoji == "🔴"

    c_starting = ContainerInfo("789", "oss_bot_db", "img", "running", "Up 5s (health: starting)", "starting", True)
    assert c_starting.status_emoji == "🟡"

    c_exited_ok = ContainerInfo("999", "oss_bot_migrate", "img", "exited", "Exited (0) 10m ago", None, True)
    assert c_exited_ok.status_emoji == "⚪"


@pytest.mark.asyncio
async def test_admin_access_middleware_reject():
    docker_mock = mock.AsyncMock(spec=DockerClient)
    middleware = AdminAccessMiddleware(admin_id=12345678, docker_client=docker_mock)
    handler = mock.AsyncMock(return_value="handled")

    # Имитация события от постороннего пользователя (id=99999999)
    bad_user = User(id=99999999, is_bot=False, first_name="Hacker")
    event = mock.MagicMock(spec=Message)
    event.answer = mock.AsyncMock()

    result = await middleware(handler, event, {"event_from_user": bad_user})

    assert result is None
    handler.assert_not_called()
    event.answer.assert_called_once()
    assert "Доступ запрещён" in event.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_admin_access_middleware_allow():
    admin_id = 12345678
    docker_mock = mock.AsyncMock(spec=DockerClient)
    middleware = AdminAccessMiddleware(admin_id=admin_id, docker_client=docker_mock)
    handler = mock.AsyncMock(return_value="success")

    admin_user = User(id=admin_id, is_bot=False, first_name="Admin")
    event = mock.MagicMock(spec=Message)

    result = await middleware(handler, event, {"event_from_user": admin_user})

    assert result == "success"
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_monitor_service_alerts():
    bot = mock.AsyncMock()
    docker = mock.AsyncMock(spec=DockerClient)

    normal_metrics = {
        "cpu_percent": 10.0,
        "cpu_count": 4,
        "ram_total": "16 GB",
        "ram_used": "4 GB",
        "ram_percent": 25.0,
        "disk_total": "100 GB",
        "disk_used": "30 GB",
        "disk_free": "70 GB",
        "disk_percent": 30.0,
        "uptime_str": "1 day",
    }

    with mock.patch("bots.telegram.monitor_service.get_system_metrics", return_value=normal_metrics):
        # 1 такт: контейнер работает штатно
        c_ok = ContainerInfo("1", "oss_bot_app", "img", "running", "Up (healthy)", "healthy", True)
        docker.list_containers.return_value = [c_ok]

        monitor = MonitorService(bot, admin_id=12345, docker_client=docker, check_interval=10, alerts_enabled=True)

        await monitor.check_all()
        bot.send_message.assert_not_called()

        # 2 такт: контейнер сбоит
        c_bad = ContainerInfo("1", "oss_bot_app", "img", "running", "Up (unhealthy)", "unhealthy", True)
        docker.list_containers.return_value = [c_bad]

        await monitor.check_all()
        assert bot.send_message.call_count == 1
        call_args = bot.send_message.call_args[1]
        assert "СБОЙ СЕРВИСА" in call_args["text"]
        assert "oss_bot_app" in call_args["text"]

        # 3 такт: контейнер всё ещё сбоит -> повторный алерт не должен слаться сразу (защита от спама)
        await monitor.check_all()
        assert bot.send_message.call_count == 1

        # 4 такт: контейнер восстановился -> отправляется сообщение о восстановлении
        docker.list_containers.return_value = [c_ok]
        await monitor.check_all()
        assert bot.send_message.call_count == 2
        recovery_args = bot.send_message.call_args[1]
        assert "ВОССТАНОВЛЕНИЕ" in recovery_args["text"]


def test_backup_button_in_keyboards():
    from bots.telegram.bot import get_main_reply_keyboard

    kb = get_main_reply_keyboard()
    button_texts = [btn.text for row in kb.keyboard for btn in row]
    assert "💾 Бэкап" in button_texts


@pytest.mark.asyncio
async def test_render_status_contains_backup_button():
    from bots.telegram.bot import render_status_content

    docker_mock = mock.AsyncMock(spec=DockerClient)
    docker_mock.list_containers.return_value = []

    text, kb = await render_status_content(docker_mock)
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "backup:create" in callbacks


@pytest.mark.asyncio
async def test_perform_database_backup_docker_success():
    import gzip

    from bots.telegram.bot import perform_database_backup
    from core.config import get_settings

    docker_mock = mock.AsyncMock(spec=DockerClient)
    c_db = ContainerInfo("10", "oss_bot_db", "postgres:16", "running", "Up", "healthy", True)
    docker_mock.list_containers.return_value = [c_db]
    docker_mock.exec_command.return_value = (0, b"CREATE TABLE test (id INT);", b"")

    settings = get_settings()
    ok, data, filename = await perform_database_backup(docker_mock, settings)

    assert ok is True
    assert data is not None
    assert filename.startswith("oss_bot_backup_")
    assert filename.endswith(".sql.gz")
    assert gzip.decompress(data) == b"CREATE TABLE test (id INT);"
    docker_mock.exec_command.assert_called_once()


@pytest.mark.asyncio
async def test_perform_database_backup_docker_failure():
    from bots.telegram.bot import perform_database_backup
    from core.config import get_settings

    docker_mock = mock.AsyncMock(spec=DockerClient)
    c_db = ContainerInfo("10", "oss_bot_db", "postgres:16", "running", "Up", "healthy", True)
    docker_mock.list_containers.return_value = [c_db]
    docker_mock.exec_command.return_value = (1, b"", b"FATAL: database does not exist")

    settings = get_settings()
    ok, data, err_msg = await perform_database_backup(docker_mock, settings)

    assert ok is False
    assert data is None
    assert "FATAL: database does not exist" in err_msg


@pytest.mark.asyncio
async def test_cmd_backup_handler():
    from bots.telegram.bot import cmd_backup
    from core.config import get_settings

    docker_mock = mock.AsyncMock(spec=DockerClient)
    c_db = ContainerInfo("10", "oss_bot_db", "postgres:16", "running", "Up", "healthy", True)
    docker_mock.list_containers.return_value = [c_db]
    docker_mock.exec_command.return_value = (0, b"DUMP CONTENT", b"")

    msg = mock.MagicMock(spec=Message)
    status_msg = mock.AsyncMock()
    msg.answer = mock.AsyncMock(return_value=status_msg)
    msg.answer_document = mock.AsyncMock()

    settings = get_settings()
    await cmd_backup(msg, docker_mock, settings)

    msg.answer.assert_called_once()
    msg.answer_document.assert_called_once()
    call_kwargs = msg.answer_document.call_args[1]
    assert "Резервная копия базы данных успешно создана" in call_kwargs["caption"]
    assert call_kwargs["document"].filename.startswith("oss_bot_backup_")


def test_rotate_old_backups(tmp_path):
    import os
    import time

    from bots.telegram.bot import rotate_old_backups

    # Создаем 3 тестовых файла: 1 свежий, 2 старых
    fresh = tmp_path / "oss_bot_backup_2026-09-18_01-00-00.sql.gz"
    fresh.write_bytes(b"fresh")

    old1 = tmp_path / "oss_bot_backup_2026-08-01_01-00-00.sql.gz"
    old1.write_bytes(b"old1")

    old2 = tmp_path / "oss_bot_backup_2026-08-02_01-00-00.sql.gz"
    old2.write_bytes(b"old2")

    # Устанавливаем mtime для старых файлов (10 и 20 дней назад)
    ten_days_ago = time.time() - (10 * 86400)
    twenty_days_ago = time.time() - (20 * 86400)
    os.utime(str(old1), (ten_days_ago, ten_days_ago))
    os.utime(str(old2), (twenty_days_ago, twenty_days_ago))

    deleted = rotate_old_backups(backup_dir=str(tmp_path), max_days=7)
    assert deleted == 2
    assert fresh.exists()
    assert not old1.exists()
    assert not old2.exists()


