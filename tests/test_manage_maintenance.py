"""тесты для утилиты scripts/manage_maintenance.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from scripts.manage_maintenance import run_cli


@pytest.mark.asyncio
async def test_manage_maintenance_status_disabled(capsys: pytest.CaptureFixture[str]) -> None:
    """проверка команды status когда техработы выключены."""
    mock_info = {
        "enabled": False,
        "message": "default",
        "bot_message": "default_bot",
        "updated_at": "",
        "updated_by": "",
    }
    with patch("core.maintenance.get_maintenance_info", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_info
        code = await run_cli(["status", "--json"])
        assert code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["enabled"] is False


@pytest.mark.asyncio
async def test_manage_maintenance_status_enabled(capsys: pytest.CaptureFixture[str]) -> None:
    """проверка команды status когда техработы включены."""
    mock_info = {
        "enabled": True,
        "message": "maint active",
        "bot_message": "bot maint active",
        "updated_at": "2026-11-13T12:00:00Z",
        "updated_by": "yen_watchdog",
    }
    with patch("core.maintenance.get_maintenance_info", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_info
        code = await run_cli(["status", "--json"])
        assert code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["enabled"] is True
        assert data["updated_by"] == "yen_watchdog"


@pytest.mark.asyncio
async def test_manage_maintenance_enable(capsys: pytest.CaptureFixture[str]) -> None:
    """проверка включения техработ через cli."""
    mock_res = {
        "enabled": True,
        "message": "custom msg",
        "bot_message": "custom bot msg",
        "updated_at": "2026-11-13T12:00:00Z",
        "updated_by": "yen_watchdog",
    }
    with patch("core.maintenance.set_maintenance_mode", new_callable=AsyncMock) as mock_set:
        mock_set.return_value = mock_res
        code = await run_cli(
            [
                "enable",
                "--message",
                "custom msg",
                "--bot-message",
                "custom bot msg",
                "--by",
                "yen_watchdog",
            ]
        )
        assert code == 0
        mock_set.assert_awaited_once_with(
            enabled=True,
            message="custom msg",
            bot_message="custom bot msg",
            updated_by="yen_watchdog",
        )
        captured = capsys.readouterr()
        assert "включен" in captured.out.lower() or "enabled" in captured.out.lower()


@pytest.mark.asyncio
async def test_manage_maintenance_disable(capsys: pytest.CaptureFixture[str]) -> None:
    """проверка выключения техработ через cli."""
    mock_res = {
        "enabled": False,
        "message": "",
        "bot_message": "",
        "updated_at": "2026-11-13T12:05:00Z",
        "updated_by": "yen_watchdog",
    }
    with patch("core.maintenance.set_maintenance_mode", new_callable=AsyncMock) as mock_set:
        mock_set.return_value = mock_res
        code = await run_cli(["disable", "--by", "yen_watchdog"])
        assert code == 0
        mock_set.assert_awaited_once_with(
            enabled=False,
            updated_by="yen_watchdog",
        )
        captured = capsys.readouterr()
        assert "выключен" in captured.out.lower() or "disabled" in captured.out.lower()


@pytest.mark.asyncio
async def test_manage_maintenance_invalid_command() -> None:
    """проверка реакции на неизвестную команду."""
    code = await run_cli(["unknown_cmd"])
    assert code != 0
