"""Тесты функционала динамического включения и отключения 2FA."""

import pytest
from httpx import ASGITransport, AsyncClient

from core.two_factor import (
    get_two_factor_info,
    is_two_factor_enabled,
    set_two_factor_mode,
)
from web.main import app


@pytest.mark.asyncio
async def test_two_factor_service_toggle():
    """Тест переключения 2FA в сервисе core.two_factor."""
    # Отключаем 2FA
    res_off = await set_two_factor_mode(False, updated_by="test_admin")
    assert res_off["enabled"] is False
    assert await is_two_factor_enabled() is False

    info_off = await get_two_factor_info()
    assert info_off["enabled"] is False
    assert info_off["updated_by"] == "test_admin"

    # Включаем 2FA обратно
    res_on = await set_two_factor_mode(True, updated_by="test_admin")
    assert res_on["enabled"] is True
    assert await is_two_factor_enabled() is True


@pytest.mark.asyncio
async def test_settings_toggle_2fa_permissions(monkeypatch):
    """Тест прав доступа к эндпоинту POST /settings/2fa."""
    from web.dependencies import require_auth
    from web.security.csrf import CSRFMiddleware

    async def mock_csrf_dispatch(self, request, call_next):
        return await call_next(request)

    monkeypatch.setattr(CSRFMiddleware, "dispatch", mock_csrf_dispatch)

    # 1. Попытка отключения без роли SUPERADMIN (обычный админ отдела)
    def mock_dept_admin():
        return {
            "username": "dept_admin",
            "role": "DEPARTMENT_ADMIN",
            "web_user_id": 10,
            "department_id": 1,
        }

    app.dependency_overrides[require_auth] = mock_dept_admin

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/settings/2fa", data={"enabled": "false", "csrf_token": "mock"})
        assert res.status_code == 403
        assert "403" in res.text

        res_json = await client.post(
            "/settings/2fa",
            data={"enabled": "false"},
            headers={"Accept": "application/json"},
        )
        assert res_json.status_code == 403
        assert "суперадминистратора" in res_json.json()["detail"]

    # 2. Успешное переключение под суперадминистратором
    def mock_superadmin():
        return {
            "username": "chief_admin",
            "role": "SUPERADMIN",
            "web_user_id": 1,
            "department_id": None,
        }

    app.dependency_overrides[require_auth] = mock_superadmin

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # AJAX запрос на отключение
        res_ajax = await client.post(
            "/settings/2fa",
            data={"enabled": "false"},
            headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
        )
        assert res_ajax.status_code == 200
        data = res_ajax.json()
        assert data["success"] is True
        assert data["enabled"] is False
        assert await is_two_factor_enabled() is False

        # Форм-запрос на включение
        res_form = await client.post(
            "/settings/2fa",
            data={"enabled": "true"},
            follow_redirects=False,
        )
        assert res_form.status_code == 303
        assert res_form.headers["location"] == "/settings/"
        assert await is_two_factor_enabled() is True

    app.dependency_overrides.clear()
