"""тесты для утилиты scripts/manage_admins.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from scripts.manage_admins import run_cli


@pytest.mark.asyncio
async def test_manage_admins_list_json(capsys: pytest.CaptureFixture[str]) -> None:
    """проверка вывода списка администраторов в формате json."""
    with patch(
        "core.telegram_admins.get_authorized_telegram_admin_ids",
        new_callable=AsyncMock,
        return_value={6665679072, 7328025089},
    ), patch(
        "core.telegram_admins.get_admin_details",
        new_callable=AsyncMock,
        return_value={"admins": [{"id": 6665679072, "role": "root"}, {"id": 7328025089, "role": "extra"}]},
    ):
        code = await run_cli(["list", "--json"])
        assert code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert 6665679072 in data["admin_ids"]
        assert 7328025089 in data["admin_ids"]


@pytest.mark.asyncio
async def test_manage_admins_add(capsys: pytest.CaptureFixture[str]) -> None:
    """проверка добавления администратора через cli."""
    with patch(
        "core.telegram_admins.add_telegram_admin_id",
        new_callable=AsyncMock,
        return_value=(True, "администратор 7328025089 успешно добавлен"),
    ) as mock_add:
        code = await run_cli(["add", "7328025089", "--note", "second account", "--by", "cli"])
        assert code == 0
        mock_add.assert_awaited_once()
        captured = capsys.readouterr()
        assert "успешно добавлен" in captured.out.lower()


@pytest.mark.asyncio
async def test_manage_admins_del(capsys: pytest.CaptureFixture[str]) -> None:
    """проверка удаления администратора через cli."""
    with patch(
        "core.telegram_admins.remove_telegram_admin_id",
        new_callable=AsyncMock,
        return_value=(True, "администратор 7328025089 успешно удален"),
    ) as mock_del:
        code = await run_cli(["del", "7328025089", "--by", "cli"])
        assert code == 0
        mock_del.assert_awaited_once()
        captured = capsys.readouterr()
        assert "успешно удален" in captured.out.lower()
