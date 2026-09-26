"""Тесты для режима технических работ (Maintenance Mode)."""

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, MagicMock
from starlette.requests import Request
from starlette.responses import Response

from core.maintenance import (
    get_maintenance_info,
    is_maintenance_mode,
    set_maintenance_mode,
)
from web.main import app
from bots.vk.bot import VKMaintenanceMiddleware


@pytest.mark.asyncio
async def test_maintenance_service_state():
    """Проверка переключения статуса и работы кеша в MaintenanceService."""
    # 1. Отключаем в начале
    await set_maintenance_mode(False, updated_by="test_admin")
    assert await is_maintenance_mode() is False

    info = await get_maintenance_info()
    assert info["enabled"] is False

    # 2. Включаем
    custom_msg = "Специальное сообщение о плановом обновлении"
    data = await set_maintenance_mode(True, message=custom_msg, updated_by="tg:12345")
    assert data["enabled"] is True
    assert data["message"] == custom_msg
    assert await is_maintenance_mode() is True

    # 3. Читаем статус
    info2 = await get_maintenance_info()
    assert info2["enabled"] is True
    assert info2["message"] == custom_msg
    assert info2["updated_by"] == "tg:12345"

    # 4. Отключаем обратно
    await set_maintenance_mode(False, updated_by="test_admin")
    assert await is_maintenance_mode() is False


@pytest.mark.asyncio
async def test_web_maintenance_middleware():
    """Проверка перехвата запросов веб-панели при активных техработах."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Включаем техработы
        await set_maintenance_mode(True, message="Сервер на обслуживании")
        try:
            # 2. Доступ к служебным маршрутам должен быть открыт (не перехватывается в maintenance.html)
            res_health = await client.get("/health")
            # /health отдаёт JSON со статусом (ok или error при отсутствии локальной БД), а не html техработ
            assert res_health.headers.get("content-type") == "application/json"
            assert res_health.json().get("status") in ("ok", "error", "degraded")

            res_maint = await client.get("/maintenance")
            assert res_maint.status_code == 200
            assert "Ведутся технические работы" in res_maint.text

            # Маршруты авторизации доступны даже во время техработ, чтобы суперадмин мог войти
            res_login = await client.get("/auth/login")
            assert res_login.status_code == 200

            # 3. Обычные страницы должны возвращать 503 и HTML-заглушку
            res_root = await client.get("/")
            assert res_root.status_code == 503
            assert "Retry-After" in res_root.headers
            assert "Ведутся технические работы" in res_root.text
            assert "Сервер на обслуживании" in res_root.text

            # 4. API запросы должны возвращать 503 JSON
            res_api = await client.get("/api/v1/tickets/", headers={"Accept": "application/json"})
            assert res_api.status_code == 503
            data = res_api.json()
            assert data["status"] == "maintenance"

        finally:
            # Сбрасываем статус техработ
            await set_maintenance_mode(False)

        # 5. При выключенных техработах обычные запросы работают
        res_after = await client.get("/auth/login")
        assert res_after.status_code == 200


@pytest.mark.asyncio
async def test_vk_maintenance_middleware():
    """Проверка перехвата сообщений в VK-боте при активных техработах."""
    # 1. При выключенных техработах middleware не должен прерывать обработку
    await set_maintenance_mode(False)
    mock_msg = MagicMock()
    mock_msg.answer = AsyncMock()
    mock_view = MagicMock()

    mw = VKMaintenanceMiddleware(event=mock_msg, view=mock_view)
    await mw.pre()
    assert mw.can_forward is True
    mock_msg.answer.assert_not_called()

    # 2. При включенных техработах middleware отправляет автоответ и останавливает событие
    await set_maintenance_mode(True, bot_message="ВК бот на техобслуживании")
    try:
        mw2 = VKMaintenanceMiddleware(event=mock_msg, view=mock_view)
        await mw2.pre()
        assert mw2.can_forward is False
        mock_msg.answer.assert_called_once()
        assert "ВК бот на техобслуживании" in mock_msg.answer.call_args[0][0]
    finally:
        await set_maintenance_mode(False)


@pytest.mark.asyncio
async def test_tg_maintenance_keyboard_and_render():
    """Проверка генерации карточки управления техработами для Telegram-бота."""
    from bots.telegram.bot import get_main_reply_keyboard, render_maintenance_content

    # 1. Проверяем наличие кнопки в клавиатуре
    kb = get_main_reply_keyboard()
    button_texts = [btn.text for row in kb.keyboard for btn in row]
    assert "🚧 Техработы" in button_texts

    # 2. Проверяем карточку при выключенных техработах
    await set_maintenance_mode(False)
    text, inline_kb = await render_maintenance_content()
    assert "ВЫКЛЮЧЕН" in text
    inline_texts = [btn.text for row in inline_kb.inline_keyboard for btn in row]
    assert "🔴 Включить техработы" in inline_texts

    # 3. Проверяем карточку при включенных техработах
    await set_maintenance_mode(True, updated_by="tg:999")
    try:
        text_on, inline_kb_on = await render_maintenance_content()
        assert "ВКЛЮЧЕН" in text_on
        assert "tg:999" in text_on
        inline_texts_on = [btn.text for row in inline_kb_on.inline_keyboard for btn in row]
        assert "🟢 Отключить техработы" in inline_texts_on
    finally:
        await set_maintenance_mode(False)
