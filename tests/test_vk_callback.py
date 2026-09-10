"""Тесты для VK Callback API (вебхуки подтверждения и событий)."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from core.config import settings
from web.main import app


@pytest.mark.asyncio
async def test_vk_callback_confirmation_success(monkeypatch):
    """Сервер возвращает VK_CONFIRMATION_TOKEN при типе события confirmation."""
    monkeypatch.setattr(settings, "VK_CONFIRMATION_TOKEN", "mock_confirmation_token_xyz")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/vk/callback",
            json={"type": "confirmation", "group_id": 12345678},
        )
        assert response.status_code == 200
        assert response.text == "mock_confirmation_token_xyz"


@pytest.mark.asyncio
async def test_vk_callback_confirmation_missing_token(monkeypatch):
    """Если токен подтверждения не задан, возвращается 500."""
    monkeypatch.setattr(settings, "VK_CONFIRMATION_TOKEN", "")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/vk/callback",
            json={"type": "confirmation", "group_id": 12345678},
        )
        assert response.status_code == 500
        assert "not configured" in response.text


@pytest.mark.asyncio
async def test_vk_callback_secret_validation(monkeypatch):
    """Проверка секрета сообщества: отклонение неверного и принятие корректного."""
    monkeypatch.setattr(settings, "VK_CALLBACK_SECRET", "super_secret_salt_42")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Неверный секрет -> 403
        bad_response = await client.post(
            "/api/v1/vk/callback",
            json={"type": "message_new", "secret": "wrong_secret", "object": {}},
        )
        assert bad_response.status_code == 403
        assert bad_response.text == "forbidden"

        # Корректный секрет -> 200 ok
        with patch("bots.vk.bot.vk_bot.process_event", new_callable=AsyncMock) as mock_process:
            good_response = await client.post(
                "/api/v1/vk/callback",
                json={"type": "message_new", "secret": "super_secret_salt_42", "object": {}},
            )
            assert good_response.status_code == 200
            assert good_response.text == "ok"
            import asyncio

            await asyncio.sleep(0.05)
            assert mock_process.called


@pytest.mark.asyncio
async def test_vk_callback_invalid_json():
    """Передача невалидного тела возвращает 400 Bad Request."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/vk/callback",
            content="not a json at all",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400
        assert response.text == "invalid json"


@pytest.mark.asyncio
async def test_vk_callback_webhooks_alias(monkeypatch):
    """Алиас /webhooks/vk работает идентично /api/v1/vk/callback."""
    monkeypatch.setattr(settings, "VK_CONFIRMATION_TOKEN", "alias_token_test")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/vk",
            json={"type": "confirmation", "group_id": 999},
        )
        assert response.status_code == 200
        assert response.text == "alias_token_test"
