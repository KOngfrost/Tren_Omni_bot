"""Тест fallback-обработчика бота ВК."""

from unittest.mock import AsyncMock, patch
import pytest
from bots.vk.bot import fallback_handler


@pytest.mark.asyncio
async def test_fallback_handler_does_not_reply():
    message = AsyncMock()
    message.from_id = 12345
    message.text = "просто произвольное сообщение"

    with patch("bots.vk.bot.touch_heartbeat") as mock_heartbeat:
        await fallback_handler(message)

        # Heartbeat обновляется
        mock_heartbeat.assert_called_once()

        # Бот НЕ отвечает и НЕ пишет "не распознал команду"
        message.answer.assert_not_called()
