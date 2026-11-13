"""тесты для хендлеров bots/telegram/handlers/admins.py и middleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Message, User

from bots.telegram.bot import AdminAccessMiddleware
from bots.telegram.handlers.admins import cmd_addadmin, cmd_admins_list, cmd_deladmin
from core.config import get_settings


def _create_mock_message(user_id: int, text: str) -> Message:
    """создать мок-объект сообщения telegram."""
    msg = MagicMock(spec=Message)
    user = MagicMock(spec=User)
    user.id = user_id
    msg.from_user = user
    msg.text = text
    msg.answer = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_cmd_admins_list() -> None:
    """проверка вызова команды /admins."""
    settings = get_settings().model_copy(update={"TELEGRAM_ADMIN_ID": 6665679072})
    msg = _create_mock_message(settings.TELEGRAM_ADMIN_ID, "/admins")

    with patch(
        "bots.telegram.handlers.admins.get_admin_details",
        new_callable=AsyncMock,
        return_value={
            "root_id": settings.TELEGRAM_ADMIN_ID,
            "admins": [
                {"id": settings.TELEGRAM_ADMIN_ID, "is_root": True, "role": "Главный тех-админ", "note": "", "added_at": ""},
                {"id": 7328025089, "is_root": False, "role": "Дополнительный админ", "note": "запасной", "added_at": "2026-11-13T12:00:00Z"},
            ],
        },
    ):
        await cmd_admins_list(msg, settings)
        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert str(settings.TELEGRAM_ADMIN_ID) in text
        assert "7328025089" in text


@pytest.mark.asyncio
async def test_cmd_addadmin_by_root() -> None:
    """проверка добавления администратора главным тех-админом."""
    settings = get_settings().model_copy(update={"TELEGRAM_ADMIN_ID": 6665679072})
    msg = _create_mock_message(settings.TELEGRAM_ADMIN_ID, "/addadmin 7328025089 второй_аккаунт")

    with patch(
        "bots.telegram.handlers.admins.add_telegram_admin_id",
        new_callable=AsyncMock,
        return_value=(True, "администратор 7328025089 успешно добавлен"),
    ) as mock_add:
        await cmd_addadmin(msg, settings)
        mock_add.assert_awaited_once_with(
            admin_id=7328025089,
            added_by=f"telegram:{settings.TELEGRAM_ADMIN_ID}",
            root_id=settings.TELEGRAM_ADMIN_ID,
            note="второй_аккаунт",
        )
        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert "успешно" in text.lower()


@pytest.mark.asyncio
async def test_cmd_addadmin_by_stranger_rejected() -> None:
    """проверка отклонения добавления администратора посторонним пользователем."""
    settings = get_settings().model_copy(update={"TELEGRAM_ADMIN_ID": 6665679072})
    msg = _create_mock_message(999999999, "/addadmin 7328025089")

    with patch("bots.telegram.handlers.admins.add_telegram_admin_id", new_callable=AsyncMock) as mock_add:
        await cmd_addadmin(msg, settings)
        mock_add.assert_not_called()
        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert "доступ запрещен" in text.lower()


@pytest.mark.asyncio
async def test_cmd_deladmin_by_root() -> None:
    """проверка удаления администратора главным тех-админом."""
    settings = get_settings().model_copy(update={"TELEGRAM_ADMIN_ID": 6665679072})
    msg = _create_mock_message(settings.TELEGRAM_ADMIN_ID, "/deladmin 7328025089")

    with patch(
        "bots.telegram.handlers.admins.remove_telegram_admin_id",
        new_callable=AsyncMock,
        return_value=(True, "администратор 7328025089 успешно удален"),
    ) as mock_del:
        await cmd_deladmin(msg, settings)
        mock_del.assert_awaited_once_with(
            admin_id=7328025089,
            root_id=settings.TELEGRAM_ADMIN_ID,
            removed_by=f"telegram:{settings.TELEGRAM_ADMIN_ID}",
        )
        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert "успешно" in text.lower()


@pytest.mark.asyncio
async def test_admin_middleware_allows_authorized_and_blocks_stranger() -> None:
    """проверка работы AdminAccessMiddleware с динамическим списком."""
    root_id = 6665679072
    extra_id = 7328025089
    stranger_id = 999999999

    docker_mock = MagicMock()
    middleware = AdminAccessMiddleware(admin_id=root_id, docker_client=docker_mock)
    handler = AsyncMock(return_value="OK")

    with patch(
        "core.telegram_admins.is_authorized_telegram_admin",
        new_callable=AsyncMock,
    ) as mock_auth:
        # 1. root_id -> разрешено
        mock_auth.return_value = True
        event_root = _create_mock_message(root_id, "/status")
        res1 = await middleware(handler, event_root, {"event_from_user": event_root.from_user})
        assert res1 == "OK"

        # 2. extra_id -> разрешено
        mock_auth.return_value = True
        event_extra = _create_mock_message(extra_id, "/status")
        res2 = await middleware(handler, event_extra, {"event_from_user": event_extra.from_user})
        assert res2 == "OK"

        # 3. stranger_id -> отклонено
        mock_auth.return_value = False
        event_stranger = _create_mock_message(stranger_id, "/status")
        res3 = await middleware(handler, event_stranger, {"event_from_user": event_stranger.from_user})
        assert res3 is None
        event_stranger.answer.assert_awaited_once()
        text = event_stranger.answer.call_args[0][0]
        assert "доступ запрещен" in text.lower()
