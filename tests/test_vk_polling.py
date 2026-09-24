"""Тесты для отказоустойчивого BotPolling (RobustBotPolling)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from vkbottle.exception_factory.base_exceptions import VKAPIError

from bots.vk.polling import RobustBotPolling


@pytest.mark.asyncio
async def test_get_server_retries_on_vk_api_error():
    """Проверка, что get_server выдерживает временную ошибку VK API (код 3) и делает ретрай."""
    polling = RobustBotPolling()
    polling.group_id = 123456

    mock_api = MagicMock()
    # Первый вызов кидает VKAPIError[3]("Not found"), второй вызов успешен
    mock_api.request = AsyncMock(
        side_effect=[
            VKAPIError[3](error_msg="Not found"),
            {"response": {"server": "https://lp.vk.com/whp/123", "key": "abc", "ts": "100"}},
        ]
    )
    polling._api = mock_api

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep, patch(
        "bots.vk.polling.touch_heartbeat"
    ) as mock_touch:
        server_data = await polling.get_server()

        assert server_data["key"] == "abc"
        assert server_data["ts"] == "100"
        assert mock_api.request.call_count == 2
        mock_sleep.assert_called_once()
        mock_touch.assert_called_once()


@pytest.mark.asyncio
async def test_get_server_resolves_group_id_with_retries():
    """Проверка автоматического разрешения group_id через groups.getById с ретраями."""
    polling = RobustBotPolling()
    polling.group_id = None

    mock_api = MagicMock()
    mock_api.request = AsyncMock(
        side_effect=[
            # groups.getById: ошибка на первой попытке
            VKAPIError[10](error_msg="Internal server error"),
            # groups.getById: успех на второй попытке
            {"response": {"groups": [{"id": 999}]}},
            # groups.getLongPollServer: успех
            {"response": {"server": "https://lp.vk.com/whp/999", "key": "key999", "ts": "1"}},
        ]
    )
    polling._api = mock_api

    with patch("asyncio.sleep", new_callable=AsyncMock):
        server_data = await polling.get_server()

        assert polling.group_id == 999
        assert server_data["key"] == "key999"
        assert mock_api.request.call_count == 3


@pytest.mark.asyncio
async def test_handle_failed_event_resets_on_exception():
    """Проверка, что при исключении в handle_failed_event возвращается пустой словарь (сервер сбрасывается)."""
    polling = RobustBotPolling()
    polling.get_server = AsyncMock(side_effect=VKAPIError[3](error_msg="Not found"))

    result = await polling.handle_failed_event(
        server={"key": "old_key", "server": "old_srv", "ts": "1"},
        event={"failed": 2},  # KEY_EXPIRED
    )

    assert result == {}


@pytest.mark.asyncio
async def test_listen_resets_server_after_consecutive_errors():
    """Проверка сброса server = None после серии ошибок (MAX_CONSECUTIVE_ERRORS_BEFORE_RESET)."""
    polling = RobustBotPolling()
    polling.group_id = 123
    polling.restore_server_ts = MagicMock(side_effect=lambda s: s)

    initial_server = {"key": "bad_key", "server": "https://lp.vk.com/bad", "ts": "1"}
    fresh_server = {"key": "good_key", "server": "https://lp.vk.com/good", "ts": "2"}

    # get_server сначала возвращает initial_server, затем fresh_server
    polling.get_server = AsyncMock(side_effect=[initial_server, fresh_server])

    # get_event: сначала 3 ошибки (имитируя мертвый ключ), затем нормальный event
    polling.get_event = AsyncMock(
        side_effect=[
            asyncio.TimeoutError(),
            asyncio.TimeoutError(),
            asyncio.TimeoutError(),
            {"ts": "3", "updates": [{"type": "message_new"}]},
        ]
    )
    polling.save_server_ts = MagicMock()

    events = []
    with patch("asyncio.sleep", new_callable=AsyncMock):
        async for event in polling.listen():
            events.append(event)
            polling.stop()
            break

    assert len(events) == 1
    assert events[0]["ts"] == "3"
    # get_server вызывался как минимум дважды (первоначальный + повторный после 3 ошибок)
    assert polling.get_server.call_count >= 2
