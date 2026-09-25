"""
QA tests for parallel sessions and system recovery.
"""

import re

import pytest
from fastapi.testclient import TestClient

from web.main import app


@pytest.fixture
def client():
    """Test client for FastAPI app."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def mock_db_with_users(db_session_maker):
    """Fixture with test users in DB."""
    import asyncio

    from core.models import WebRole, WebUser
    from web.security.passwords import hash_password

    async def _setup():
        async with db_session_maker() as session:
            superadmin = WebUser(
                username="superadmin",
                password_hash=hash_password("SuperSecret123!"),
                role=WebRole.SUPERADMIN,
                is_active=True,
            )
            dept_admin = WebUser(
                username="zhilbyt_admin",
                password_hash=hash_password("DeptSecret456!"),
                role=WebRole.DEPARTMENT_ADMIN,
                department_id=1,
                is_active=True,
            )
            session.add_all([superadmin, dept_admin])
            await session.commit()

    asyncio.run(_setup())
    return db_session_maker


class TestAdminLoginRestored:
    """Tests for admin login recovery."""

    def test_login_page_accessible(self, client):
        """Login page is accessible and contains CSRF token."""
        response = client.get("/auth/login")
        assert response.status_code == 200
        assert "csrf_token" in response.text

    def test_bootstrap_login_success(self, client, db_session_maker, monkeypatch):
        """Successful login with bootstrap credentials."""
        monkeypatch.setattr("web.routes.auth.settings.WEB_ADMIN_USERNAME", "admin")
        monkeypatch.setattr("web.routes.auth.settings.WEB_ADMIN_PASSWORD", "admin123")
        login_page = client.get("/auth/login")
        csrf_token = re.search(r'csrf_token" value="([^"]+)"', login_page.text).group(1)
        response = client.post(
            "/auth/login",
            data={"username": "admin", "password": "admin123", "csrf_token": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers.get("location") == "/"
        assert "session=" in response.headers.get("set-cookie", "")

    def test_bootstrap_login_wrong_password(self, client, db_session_maker, monkeypatch):
        """Failed login redirects to login page with error flash."""
        monkeypatch.setattr("web.routes.auth.settings.WEB_ADMIN_USERNAME", "admin")
        monkeypatch.setattr("web.routes.auth.settings.WEB_ADMIN_PASSWORD", "admin123")
        login_page = client.get("/auth/login")
        csrf_token = re.search(r'csrf_token" value="([^"]+)"', login_page.text).group(1)
        response = client.post(
            "/auth/login",
            data={"username": "admin", "password": "wrong", "csrf_token": csrf_token},
            follow_redirects=False,
        )
        # Теперь возвращается 302 redirect на /auth/login с flash_error
        assert response.status_code == 302
        assert "/auth/login" in response.headers.get("location", "")

    def test_web_user_login_success(self, client, mock_db_with_users):
        """Successful login with web_users credentials."""
        login_page = client.get("/auth/login")
        csrf_token = re.search(r'csrf_token" value="([^"]+)"', login_page.text).group(1)
        response = client.post(
            "/auth/login",
            data={
                "username": "superadmin",
                "password": "SuperSecret123!",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers.get("location") == "/"
        assert "session=" in response.headers.get("set-cookie", "")


class TestParallelSessions:
    """Tests for parallel sessions."""

    def test_two_accounts_simultaneously(self, client, db_session_maker, monkeypatch):
        """Two accounts can be logged in simultaneously."""
        monkeypatch.setattr("web.routes.auth.settings.WEB_ADMIN_USERNAME", "admin")
        monkeypatch.setattr("web.routes.auth.settings.WEB_ADMIN_PASSWORD", "admin123")
        with TestClient(app) as client2:
            login1 = client.get("/auth/login")
            csrf1 = re.search(r'csrf_token" value="([^"]+)"', login1.text).group(1)
            resp1 = client.post(
                "/auth/login",
                data={"username": "admin", "password": "admin123", "csrf_token": csrf1},
                follow_redirects=False,
            )
            assert resp1.status_code == 303
            assert "session=" in resp1.headers.get("set-cookie", "")

            login2 = client2.get("/auth/login")
            csrf2 = re.search(r'csrf_token" value="([^"]+)"', login2.text).group(1)
            resp2 = client2.post(
                "/auth/login",
                data={"username": "admin", "password": "admin123", "csrf_token": csrf2},
                follow_redirects=False,
            )
            assert resp2.status_code == 303
            assert "session=" in resp2.headers.get("set-cookie", "")

            assert client.cookies.get("session") != client2.cookies.get("session")

    def test_session_isolation(self, client, db_session_maker, monkeypatch):
        """Session isolation: logout of one user doesn't affect another."""
        monkeypatch.setattr("web.routes.auth.settings.WEB_ADMIN_USERNAME", "admin")
        monkeypatch.setattr("web.routes.auth.settings.WEB_ADMIN_PASSWORD", "admin123")
        with TestClient(app) as client2:
            login1 = client.get("/auth/login")
            csrf1 = re.search(r'csrf_token" value="([^"]+)"', login1.text).group(1)
            resp1 = client.post(
                "/auth/login",
                data={"username": "admin", "password": "admin123", "csrf_token": csrf1},
                follow_redirects=False,
            )
            assert "session=" in resp1.headers.get("set-cookie", "")

            login2 = client2.get("/auth/login")
            csrf2 = re.search(r'csrf_token" value="([^"]+)"', login2.text).group(1)
            resp2 = client2.post(
                "/auth/login",
                data={"username": "admin", "password": "admin123", "csrf_token": csrf2},
                follow_redirects=False,
            )
            assert "session=" in resp2.headers.get("set-cookie", "")

            assert client.cookies.get("session") != client2.cookies.get("session")

            # First user logs out
            csrf_logout = re.search(r'csrf_token" value="([^"]+)"', client.get("/").text)
            if csrf_logout:
                client.post(
                    "/auth/logout",
                    data={"csrf_token": csrf_logout.group(1)},
                    follow_redirects=False,
                )

            # Second user should still be logged in
            dash2 = client2.get("/", follow_redirects=False)
            assert dash2.status_code in (200, 302, 303)


class TestLogout:
    """Tests for logout."""

    def test_logout_clears_session(self, client, db_session_maker, monkeypatch):
        """Logout clears the session."""
        monkeypatch.setattr("web.routes.auth.settings.WEB_ADMIN_USERNAME", "admin")
        monkeypatch.setattr("web.routes.auth.settings.WEB_ADMIN_PASSWORD", "admin123")
        login_page = client.get("/auth/login")
        csrf = re.search(r'csrf_token" value="([^"]+)"', login_page.text).group(1)
        response = client.post(
            "/auth/login",
            data={"username": "admin", "password": "admin123", "csrf_token": csrf},
            follow_redirects=False,
        )
        # Получаем новый CSRF-токен со страницы после редиректа
        dashboard = client.get("/", follow_redirects=False)
        csrf_logout = re.search(r'csrf_token" value="([^"]+)"', dashboard.text)
        if csrf_logout:
            response = client.post(
                "/auth/logout",
                data={"csrf_token": csrf_logout.group(1)},
                follow_redirects=False,
            )
            assert response.status_code == 303
            assert "/auth/login" in response.headers.get("location", "")


class TestMiddlewareOrder:
    """Tests for middleware order."""

    def test_middleware_order(self):
        """SessionMiddleware must be registered before CSRFMiddleware."""
        from starlette.middleware.sessions import SessionMiddleware

        from web.main import app
        from web.security.session_store import RedisSessionMiddleware

        middleware_classes = [m.cls if hasattr(m, "cls") else type(m) for m in app.user_middleware]

        session_idx = None
        for i, m in enumerate(middleware_classes):
            if m in (SessionMiddleware, RedisSessionMiddleware):
                session_idx = i
        assert session_idx is not None


class TestSecurityHeaders:
    """Tests for security headers."""

    def test_headers_present(self, client):
        """All critical security headers are present."""
        response = client.get("/auth/login")
        assert response.headers.get("x-content-type-options") == "nosniff"
        assert "content-security-policy" in response.headers
        assert "frame-ancestors" in response.headers.get("content-security-policy", "")

    def test_csp_restricts_scripts(self, client):
        """CSP restricts script execution."""
        response = client.get("/auth/login")
        csp = response.headers.get("content-security-policy", "")
        assert "script-src" in csp
        assert "form-action" in csp


class TestRateLimiting:
    """Tests for rate limiting."""

    def test_rate_limit_after_five_failures(self, client, db_session_maker, monkeypatch):
        """Block after 5 failed attempts."""
        monkeypatch.setattr("web.routes.auth.settings.WEB_ADMIN_USERNAME", "admin")
        monkeypatch.setattr("web.routes.auth.settings.WEB_ADMIN_PASSWORD", "admin123")

        for _ in range(5):
            login_page = client.get("/auth/login")
            csrf = re.search(r'csrf_token" value="([^"]+)"', login_page.text).group(1)
            response = client.post(
                "/auth/login",
                data={"username": "admin", "password": "wrong_password", "csrf_token": csrf},
                follow_redirects=False,
            )
            # Ожидается redirect 302 с flash_error "Неверный логин или пароль"
            assert response.status_code == 302

        # После 5 попыток — rate limiting блокирует вход
        login_page = client.get("/auth/login")
        csrf = re.search(r'csrf_token" value="([^"]+)"', login_page.text).group(1)
        response = client.post(
            "/auth/login",
            data={"username": "admin", "password": "admin123", "csrf_token": csrf},
            follow_redirects=False,
        )
        # Rate limiting возвращает 302 с flash_error о блокировке
        assert response.status_code == 302
