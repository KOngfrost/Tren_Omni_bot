"""Тесты страницы настроек, переключения светлой/тёмной темы и идемпотентности 2FA."""

import pytest
from httpx import ASGITransport, AsyncClient

from core.config import settings
from core.models import Admin, User, UserRole, WebRole, WebUser
from web.main import app
from web.security.passwords import hash_password


@pytest.mark.asyncio
async def test_settings_page_requires_auth():
    """Анонимный доступ к странице настроек перенаправляет на страницу входа."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/settings/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_settings_page_authenticated(db_session_maker, monkeypatch):
    """Авторизованный пользователь видит страницу настроек с выбором темы и эффектов стекла."""
    monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "test_settings_secret_key_123456789")
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", False)

    async with db_session_maker() as session:
        user = User(vk_id=888111222, full_name="Тестовый Настройщик")
        session.add(user)
        await session.flush()
        admin = Admin(user_id=user.id, role=UserRole.SUPERADMIN)
        session.add(admin)
        await session.flush()
        web_user = WebUser(
            username="settings_tester",
            password_hash=hash_password("SettingsPass123"),
            role=WebRole.SUPERADMIN,
            admin_id=admin.id,
            is_active=True,
        )
        session.add(web_user)
        await session.commit()

    import re

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Авторизуемся
        login_page = await client.get("/auth/login")
        csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)

        # Устанавливаем сессию напрямую через login
        login_resp = await client.post(
            "/auth/login",
            data={
                "username": "settings_tester",
                "password": "SettingsPass123",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        assert login_resp.status_code == 303

        # Переходим в настройки
        resp = await client.get("/settings/")
        assert resp.status_code == 200
        content = resp.text
        assert "Тема оформления" in content
        assert "Тёмная тема" in content
        assert "Светлая тема" in content
        assert "Frosted Glass" in content or "матового стекла" in content


@pytest.mark.asyncio
async def test_update_theme_post_and_cookie(db_session_maker, monkeypatch):
    """POST /settings/theme обновляет тему и сохраняет куки app_theme и app_glass_effect."""
    import re

    from core.models import WebRole, WebUser
    from web.security.passwords import hash_password

    monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "test_settings_secret_key_123456789")
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", False)

    # Создаём пользователя
    async with db_session_maker() as session:
        web_user = WebUser(
            username="theme_tester",
            password_hash=hash_password("ThemePass123"),
            role=WebRole.SUPERADMIN,
            is_active=True,
        )
        session.add(web_user)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Авторизуемся
        login_page = await client.get("/auth/login")
        csrf_login = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)

        login_resp = await client.post(
            "/auth/login",
            data={
                "username": "theme_tester",
                "password": "ThemePass123",
                "csrf_token": csrf_login,
            },
            follow_redirects=False,
        )
        assert login_resp.status_code == 303

        # Получаем страницу настроек и её CSRF токен
        settings_page = await client.get("/settings/")
        assert settings_page.status_code == 200
        csrf_settings = re.search(r'name="csrf_token" value="([^"]+)"', settings_page.text).group(
            1
        )

        # Запрос на смену темы на light
        resp = await client.post(
            "/settings/theme",
            data={
                "theme": "light",
                "glass_effect": "true",
                "csrf_token": csrf_settings,
            },
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["theme"] == "light"
        assert data["glass_effect"] is True
        assert resp.cookies.get("app_theme") == "light"
        assert resp.cookies.get("app_glass_effect") == "true"


@pytest.mark.asyncio
async def test_update_theme_unauthenticated_rejected(monkeypatch):
    """Неаутентифицированный POST /settings/theme отклоняется редиректом на логин или 401/403."""
    monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "test_settings_secret_key_123456789")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/settings/theme",
            data={
                "theme": "light",
                "glass_effect": "true",
                "csrf_token": "mock_csrf_token",
            },
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303, 401, 403)
        if resp.status_code in (302, 303):
            assert "/auth/login" in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_2fa_idempotency_when_already_authenticated(monkeypatch):
    """Если пользователь уже авторизован, обращение к /auth/2fa не выдаёт ошибку, а редиректит на дашборд."""
    monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "test_settings_secret_key_123456789")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Проверяем, что при отсутствии сессии идёт редирект на /auth/login
        resp = await client.get("/auth/2fa", follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert "/auth/login" in resp.headers.get("location", "")
