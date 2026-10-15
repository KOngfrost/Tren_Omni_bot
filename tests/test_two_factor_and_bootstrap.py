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
        assert (
            result is None
        ), "Bootstrap должен быть заблокирован при наличии постоянного суперадмина"


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


@pytest.mark.asyncio
async def test_2fa_bypass_hardening_in_production(monkeypatch):
    """В production и при не-test режиме префиксы qa_/test_/temp_ и expires_at не обходят 2FA."""
    from datetime import UTC, datetime, timedelta

    from web.routes.auth import _is_temporary_web_user

    qa_user = WebUser(username="qa_superadmin")
    test_user = WebUser(username="test_superadmin")
    temp_user = WebUser(username="temp_superadmin")
    exp_user = WebUser(username="regular_admin", expires_at=datetime.now(UTC) + timedelta(days=1))

    # 1. В тестовом режиме байпас работает
    monkeypatch.setattr(settings, "APP_ENV", "test")
    monkeypatch.setattr(settings, "TESTING", True)
    assert _is_temporary_web_user(qa_user) is True
    assert _is_temporary_web_user(test_user) is True
    assert _is_temporary_web_user(temp_user) is True
    assert _is_temporary_web_user(exp_user) is True

    # 2. В production режиме байпас ЗАПРЕЩЁН
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "TESTING", False)
    assert _is_temporary_web_user(qa_user) is False
    assert _is_temporary_web_user(test_user) is False
    assert _is_temporary_web_user(temp_user) is False
    assert _is_temporary_web_user(exp_user) is False

    # 3. При APP_ENV="production" даже с TESTING=True байпас ЗАПРЕЩЁН
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "TESTING", True)
    assert _is_temporary_web_user(qa_user) is False

    # 4. При TESTING=False даже с APP_ENV != "production" байпас ЗАПРЕЩЁН
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "TESTING", False)
    assert _is_temporary_web_user(qa_user) is False


@pytest.mark.asyncio
async def test_authenticate_blocks_unlinked_qa_user_in_production(db_session_maker, monkeypatch):
    """В production пользователь с префиксом qa_ без привязки к VK не может войти в обход 2FA."""
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", True)

    async with db_session_maker() as session:
        user = WebUser(
            username="qa_hacker",
            password_hash=hash_password("qa_hacker_pass"),
            role=WebRole.SUPERADMIN,
            is_active=True,
            two_factor_enabled=True,
        )
        session.add(user)
        await session.commit()

        # В production: вход заблокирован
        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "TESTING", False)
        result_prod = await _authenticate(session, "qa_hacker", "qa_hacker_pass")
        assert (
            result_prod is None
        ), "В production 2FA байпас по префиксу логина должен быть заблокирован"

        # В тестовом режиме: вход разрешён
        monkeypatch.setattr(settings, "APP_ENV", "test")
        monkeypatch.setattr(settings, "TESTING", True)
        result_test = await _authenticate(session, "qa_hacker", "qa_hacker_pass")
        assert result_test is not None
        assert result_test["needs_2fa"] is False
