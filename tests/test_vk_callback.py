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
async def test_vk_callback_secret_validation(monkeypatch, db_session_maker):
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
async def test_vk_callback_durable_inbox_deduplication(db_session_maker):
    """Durable Inbox сохраняет событие в БД и идемпотентно дедуплицирует повторные запросы."""
    from sqlalchemy import select

    from core.models import VkInbox

    payload = {
        "type": "message_new",
        "event_id": "unique_event_abc_123",
        "group_id": 99999,
        "object": {"message": {"text": "hello"}},
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("bots.vk.bot.vk_bot.process_event", new_callable=AsyncMock) as mock_process:
            # Первый вызов: сохранение в inbox и диспетчеризация
            resp1 = await client.post("/api/v1/vk/callback", json=payload)
            assert resp1.status_code == 200
            assert resp1.text == "ok"

            import asyncio

            await asyncio.sleep(0.05)
            assert mock_process.call_count == 1

            # Проверяем, что событие сохранено в таблице vk_inbox
            async with db_session_maker() as session:
                entry = await session.scalar(
                    select(VkInbox).where(VkInbox.event_id == "unique_event_abc_123")
                )
                assert entry is not None
                assert entry.event_type == "message_new"
                assert entry.group_id == 99999
                assert entry.status == "processed"

            # Второй вызов (повторный replay того же event_id): возвращает 'ok' без повторной диспетчеризации
            resp2 = await client.post("/api/v1/vk/callback", json=payload)
            assert resp2.status_code == 200
            assert resp2.text == "ok"

            await asyncio.sleep(0.05)
            # Счётчик вызовов обработчика не должен измениться
            assert mock_process.call_count == 1


@pytest.mark.asyncio
async def test_vk_callback_database_error_returns_500(monkeypatch, db_session_maker):
    """При ошибке записи в хранилище inbox эндпоинт возвращает 500 для повтора со стороны VK."""
    from unittest.mock import MagicMock

    bad_maker = MagicMock(side_effect=RuntimeError("Database storage is unavailable"))
    monkeypatch.setattr("web.routes.vk_callback.async_session_maker", bad_maker)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/vk/callback",
            json={"type": "message_new", "event_id": "fail_evt_999", "object": {}},
        )
        assert resp.status_code == 500


@pytest.mark.asyncio
async def test_process_pending_inbox_events(db_session_maker):
    """Обработка зависших событий inbox из БД после перезапуска сервиса."""
    import json

    from core.models import VkInbox
    from web.routes.vk_callback import process_pending_inbox_events

    async with db_session_maker() as session:
        inbox_item = VkInbox(
            event_id="pending_event_456",
            event_type="message_new",
            payload=json.dumps({"type": "message_new", "text": "test_msg"}),
            status="pending",
        )
        session.add(inbox_item)
        await session.commit()
        item_id = inbox_item.id

    with patch("bots.vk.bot.vk_bot.process_event", new_callable=AsyncMock) as mock_process:
        processed_count = await process_pending_inbox_events()
        assert processed_count == 1
        assert mock_process.called

    async with db_session_maker() as session:
        updated = await session.get(VkInbox, item_id)
        assert updated is not None
        assert updated.status == "processed"
        assert updated.processed_at is not None


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
