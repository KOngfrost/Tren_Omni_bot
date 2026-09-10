"""Тесты двухфакторной аутентификации (2FA через VK) и безопасности bootstrap-входа."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from core.config import settings
from core.models import Admin, User, UserRole, VkOutbox, WebRole, WebUser
from web.main import app
from web.routes.auth import _authenticate
from web.security.passwords import hash_password


@pytest.mark.asyncio
async def test_bootstrap_blocked_when_superadmin_exists(db_session_maker, monkeypatch):
    """Bootstrap-вход блокируется, если в базе есть активный постоянный суперадминистратор."""
    monkeypatch.setattr(settings, "WEB_ADMIN_USERNAME", "admin_bootstrap")
    monkeypatch.setattr(settings, "WEB_ADMIN_PASSWORD", "bootstrap_pass_123")
    monkeypatch.setattr(settings, "FORCE_BOOTSTRAP_OVERRIDE", False)

    async with db_session_maker() as session:
        user = WebUser(
            username="existing_superadmin",
            password_hash=hash_password("pass123"),
            role=WebRole.SUPERADMIN,
            is_active=True,
        )
        session.add(user)
        await session.commit()

        result = await _authenticate(session, "admin_bootstrap", "bootstrap_pass_123")
        assert result is None, (
            "Bootstrap должен быть заблокирован при наличии постоянного суперадмина"
        )


@pytest.mark.asyncio
async def test_bootstrap_allowed_on_empty_db(db_session_maker, monkeypatch):
    """Bootstrap-вход разрешён, если в базе нет постоянных суперадминистраторов."""
    monkeypatch.setattr(settings, "WEB_ADMIN_USERNAME", "admin_bootstrap")
    monkeypatch.setattr(settings, "WEB_ADMIN_PASSWORD", "bootstrap_pass_123")
    monkeypatch.setattr(settings, "BOOTSTRAP_ALLOWED", True)

    async with db_session_maker() as session:
        result = await _authenticate(session, "admin_bootstrap", "bootstrap_pass_123")
        assert result is not None
        assert result["username"] == "admin_bootstrap"
        assert result["bootstrap"] is True


@pytest.mark.asyncio
async def test_2fa_flow_with_vk_admin(db_session_maker, monkeypatch):
    """Проверка полного цикла 2FA для администратора с привязанным VK ID."""
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", True)
    monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "test_secret_for_2fa_testing_key_1234567")

    async with db_session_maker() as session:
        # Создаём администратора VK
        vk_user = User(vk_id=987654321, full_name="Тест Админ")
        session.add(vk_user)
        await session.flush()
        admin = Admin(user_id=vk_user.id, role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()

        # Создаём веб-пользователя с привязкой к админу
        web_user = WebUser(
            username="vk_admin_user",
            password_hash=hash_password("secure_pass_2fa"),
            role=WebRole.SUPERADMIN,
            admin_id=admin.id,
            is_active=True,
        )
        session.add(web_user)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Запрос страницы входа для получения CSRF
        login_page_res = await client.get("/auth/login")
        assert login_page_res.status_code == 200
        import re

        csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', login_page_res.text)
        assert csrf_match, "CSRF token not found in login page"
        csrf = csrf_match.group(1)

        # 2. Логин
        login_res = await client.post(
            "/auth/login",
            data={
                "username": "vk_admin_user",
                "password": "secure_pass_2fa",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        # Должен перенаправить на /auth/2fa
        assert login_res.status_code == 303
        assert login_res.headers["location"] == "/auth/2fa"

        # Проверяем, что в очереди outbox появилось сообщение с кодом
        async with db_session_maker() as session:
            outbox_msg = (
                await session.execute(
                    select(VkOutbox)
                    .where(VkOutbox.vk_id == 987654321)
                    .order_by(VkOutbox.id.desc())
                )
            ).scalar_one_or_none()
            assert outbox_msg is not None
            assert "🔐 Одноразовый код для входа" in outbox_msg.text

            match = re.search(r"\b(\d{6})\b", outbox_msg.text)
            assert match is not None
            otp_code = match.group(1)

        # 3. Переход на страницу 2FA
        page_2fa = await client.get("/auth/2fa")
        assert page_2fa.status_code == 200
        assert "Подтверждение 2FA" in page_2fa.text
        csrf_2fa_match = re.search(r'name="csrf_token" value="([^"]+)"', page_2fa.text)
        assert csrf_2fa_match
        csrf_2fa = csrf_2fa_match.group(1)

        # 4. Проверка ввода неверного кода
        wrong_res = await client.post(
            "/auth/2fa",
            data={"code": "000000", "csrf_token": csrf_2fa},
            follow_redirects=False,
        )
        assert wrong_res.status_code == 303
        assert wrong_res.headers["location"] == "/auth/2fa"

        # 5. Проверка ввода правильного кода
        correct_res = await client.post(
            "/auth/2fa",
            data={"code": otp_code, "csrf_token": csrf_2fa},
            follow_redirects=False,
        )
        assert correct_res.status_code == 303
        assert correct_res.headers["location"] == "/"

        # 6. Доступ к дашборду теперь разрешён
        dash_res = await client.get("/")
        assert dash_res.status_code == 200
        assert "vk_admin_user" in dash_res.text
