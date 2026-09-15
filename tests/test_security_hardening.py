"""Тесты приемки Пакета 1: критическая безопасность и авторизация.

Покрывает критерии приемки:
- Ошибка #1: cookie в fallback-режиме не позволяет извлечь OTP-код
  (в сессии хранится только криптографический хеш + токен, не открытый код).
- Ошибка #2: 2FA обязательна для всех привилегированных учёток — вход без
  доверенного VK-канала блокируется; bootstrap-сессия инвалидируется при
  отключении резервного входа.
- Ошибка #3: Fail-Closed — при недоступной БД доступ запрещается (503),
  деактивированный пользователь мгновенно теряет доступ.
- Ошибка #4: chunked-запрос с телом больше лимита разрывается с 413
  (фактический подсчёт байтов, а не только Content-Length).
"""

import base64
import json
import re

import pytest
from httpx import ASGITransport, AsyncClient

from core.config import settings
from core.models import WebRole, WebUser
from web.main import app
from web.routes.auth import _authenticate, bootstrap_session_still_valid
from web.security.passwords import hash_password

# ==========================================
# Ошибка #1: OTP не извлекается из cookie
# ==========================================


@pytest.mark.asyncio
async def test_otp_code_never_in_session_cookie(db_session_maker, monkeypatch):
    """Cookie в fallback-режиме не позволяет извлечь OTP-код."""
    from web.security import otp_store

    class _FakeRequest:
        def __init__(self):
            self.session = {}

    request = _FakeRequest()
    otp_code = await otp_store.begin(
        request,
        user_data={"username": "victim_admin", "role": "SUPERADMIN", "web_user_id": 1},
        vk_admin_id=987654321,
        ttl=settings.TWO_FACTOR_CODE_TTL,
    )
    assert re.fullmatch(r"\d{6}", otp_code), otp_code

    # Злоумышленник декодирует содержимое сессии/cookie
    raw = request.session[otp_store.SESSION_KEY]
    cookie_payload = base64.b64encode(json.dumps(raw).encode()).decode()
    decoded = base64.b64decode(cookie_payload).decode()

    assert otp_code not in decoded
    state = json.loads(decoded)
    assert "code" not in state
    assert "code_hash" in state
    assert state["code_hash"] != otp_code

    # Успешная валидация уничтожает попытку (одноразовый токен)
    result = await otp_store.verify(request, otp_code)
    assert result.ok
    assert otp_store.SESSION_KEY not in request.session
    result2 = await otp_store.verify(request, otp_code)
    assert not result2.ok


@pytest.mark.asyncio
async def test_wrong_otp_cannot_be_recovered_from_hash(db_session_maker):
    """Хеш кода в сессии невосстановим: подбор неверного кода не проходит."""
    from web.security import otp_store

    class _FakeRequest:
        def __init__(self):
            self.session = {}

    request = _FakeRequest()
    code = await otp_store.begin(
        request,
        user_data={"username": "victim_admin", "role": "SUPERADMIN", "web_user_id": 1},
        vk_admin_id=987654321,
        ttl=300,
    )
    state = request.session[otp_store.SESSION_KEY]
    assert code not in state["code_hash"]
    wrong = code[:-1] + ("0" if code[-1] != "0" else "1")
    result = await otp_store.verify(request, wrong)
    assert result.status == "invalid"


# ==========================================
# Ошибка #2: 2FA обязательна для всех привилегированных учёток
# ==========================================


@pytest.mark.asyncio
async def test_admin_without_vk_channel_blocked_when_2fa_enabled(db_session_maker, monkeypatch):
    """Администратор без привязанного VK-аккаунта не может войти при TWO_FACTOR_ENABLED."""
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", True)

    async with db_session_maker() as session:
        web_user = WebUser(
            username="no_channel_admin",
            password_hash=hash_password("pass_12345"),
            role=WebRole.SUPERADMIN,
            is_active=True,
        )
        session.add(web_user)
        await session.commit()

        result = await _authenticate(session, "no_channel_admin", "pass_12345")
        assert result is None, "Fail-Closed: нет доверенного канала — вход невозможен"


@pytest.mark.asyncio
async def test_bootstrap_blocked_without_2fa_channel_when_2fa_enabled(
    db_session_maker, monkeypatch
):
    """Bootstrap-вход блокируется, если 2FA включена, а OTP-канал не настроен."""
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", True)
    monkeypatch.setattr(settings, "WEB_ADMIN_USERNAME", "bootstrap_admin")
    monkeypatch.setattr(settings, "WEB_ADMIN_PASSWORD", "bootstrap_pass_123")
    monkeypatch.setattr(settings, "BOOTSTRAP_ALLOWED", True)
    monkeypatch.setattr(settings, "WEB_ADMIN_2FA_VK_ID", 0)
    monkeypatch.setattr(settings, "VK_REPORT_ADMIN_ID", 0)

    async with db_session_maker() as session:
        result = await _authenticate(session, "bootstrap_admin", "bootstrap_pass_123")
        assert result is None, "Невозможно авторизоваться без 2FA: OTP-канал не настроен"


def test_bootstrap_session_invalidated_when_bootstrap_disabled(monkeypatch):
    """Bootstrap-сессия не доверяет устаревшим данным: вход отключён — доступ отозван."""
    monkeypatch.setattr(settings, "BOOTSTRAP_ALLOWED", False)
    monkeypatch.setattr(settings, "WEB_ADMIN_USERNAME", "testadmin")
    monkeypatch.setattr(settings, "WEB_ADMIN_PASSWORD", "test_password_123")

    stale_session_user = {
        "username": "testadmin",
        "role": "SUPERADMIN",
        "bootstrap": True,
        "web_user_id": None,
        "department_id": None,
    }
    assert bootstrap_session_still_valid(stale_session_user) is False


def test_bootstrap_session_invalidated_when_username_changed(monkeypatch):
    """Креды .env изменены — прежняя bootstrap-сессия больше не действительна."""
    monkeypatch.setattr(settings, "BOOTSTRAP_ALLOWED", True)
    monkeypatch.setattr(settings, "WEB_ADMIN_USERNAME", "new_admin")
    monkeypatch.setattr(settings, "WEB_ADMIN_PASSWORD", "new_pass")

    stale_session_user = {
        "username": "old_admin",
        "role": "SUPERADMIN",
        "bootstrap": True,
        "web_user_id": None,
        "department_id": None,
    }
    assert bootstrap_session_still_valid(stale_session_user) is False


# ==========================================
# Ошибка #3: Fail-Closed авторизация
# ==========================================


@pytest.mark.asyncio
async def test_deactivated_user_blocked_immediately(db_session_maker, monkeypatch):
    """Деактивированный в БД пользователь мгновенно теряет доступ."""
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", False)

    async with db_session_maker() as session:
        web_user = WebUser(
            username="doomed_admin",
            password_hash=hash_password("pass_12345"),
            role=WebRole.DEPARTMENT_ADMIN,
            is_active=True,
        )
        session.add(web_user)
        await session.commit()
        web_user_id = web_user.id

    # Пользователь деактивируется ПОСЛЕ выдачи пароля (между входами)
    async with db_session_maker() as session:
        web_user = await session.get(WebUser, web_user_id)
        web_user.is_active = False
        await session.commit()

    async with db_session_maker() as session:
        result = await _authenticate(session, "doomed_admin", "pass_12345")
        assert result is None


@pytest.mark.asyncio
async def test_require_auth_fail_closed_on_db_failure(db_session_maker, monkeypatch):
    """Fail-Closed (#3): сбой БД при require_auth — 503, а не доверие cookie."""
    from fastapi import HTTPException

    from web import dependencies as deps
    from web.dependencies import require_auth

    class _FakeRequest:
        url = type("U", (), {"path": "/tickets/"})()
        headers = {}
        session = {
            "user": {
                "username": "session_admin",
                "role": "SUPERADMIN",
                "web_user_id": 4242,
            }
        }

        def clear(self):
            self.session.clear()

    class _FailingSessionMaker:
        """Имитация sessionmaker: соединение не устанавливается (сетевой сбой)."""

        async def __aenter__(self):
            raise RuntimeError("database unavailable")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    request = _FakeRequest()

    monkeypatch.setattr(deps, "async_session_maker", _FailingSessionMaker)

    with pytest.raises(HTTPException) as exc_info:
        await require_auth(request)
    assert exc_info.value.status_code == 503


# ==========================================
# Ошибка #4: защита от потоковой передачи (chunked → 413)
# ==========================================


@pytest.mark.asyncio
async def test_chunked_body_over_limit_rejected_with_413():
    """Chunked-запрос без Content-Length, тело больше лимита → 413."""
    from web.security.middleware import RequestSizeLimitMiddleware

    limit = 1024
    big_body = b"x" * (limit + 512)

    received: list[dict] = []

    async def inner_app(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            received.append(message)

    async def receive():
        return {"type": "http.request", "body": big_body, "more_body": False}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    middleware = RequestSizeLimitMiddleware(inner_app, max_body_size=limit)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/tickets/",
        "headers": [(b"transfer-encoding", b"chunked")],
    }
    await middleware(scope, receive, send)

    assert sent, "Ответ 413 обязателен"
    assert sent[0]["status"] == 413
    assert received == [], "Тело не должно достигнуть обработчика"


@pytest.mark.asyncio
async def test_chunked_body_within_limit_passes_through():
    """Chunked-запрос в пределах лимита проходит к обработчику целиком."""
    from web.security.middleware import RequestSizeLimitMiddleware

    limit = 1024
    body = b"y" * 512

    received: list[bytes] = []

    async def inner_app(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            received.append(message.get("body", b""))

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        pass

    middleware = RequestSizeLimitMiddleware(inner_app, max_body_size=limit)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/tickets/",
        "headers": [(b"transfer-encoding", b"chunked")],
    }
    await middleware(scope, receive, send)

    assert b"".join(received) == body, "Обработчик получает поток целиком"


# ==========================================
# Раздел 5 ТЗ: bootstrap-режим виден и блокируется стражем старта
# ==========================================


def _prod_settings(**overrides):
    """Конфигурация, похожая на production, для тестов стража старта.

    Локальный .env намеренно переопределён нейтральными значениями, чтобы
    проверки не зависели от того, какие креды заданы у разработчика.
    """
    from core.config import Settings

    base = {
        "APP_ENV": "production",
        "DB_USER": "oss_prod",
        "DB_PASS": "s3cret-pass-not-default",
        "VK_BOT_TOKEN": "vk-token",
        "ADMIN_VK_IDS": {1},
        "SESSION_SECRET_KEY": "x" * 48,
        "WEB_ADMIN_USERNAME": "",
        "WEB_ADMIN_PASSWORD": "",
        "WEB_ADMIN_2FA_VK_ID": 0,
        "VK_REPORT_ADMIN_ID": 0,
    }
    base.update(overrides)
    return Settings(**base)


def test_bootstrap_guard_blocks_placeholder_session_secret():
    """Страж старта прерывает запуск с placeholder-секретом в production."""
    from core.startup_guard import enforce_startup_security

    settings_with_placeholder = _prod_settings(
        SESSION_SECRET_KEY="change_me_session_secret_change_me_session_secret"
    )
    with pytest.raises(RuntimeError, match="Placeholder"):
        enforce_startup_security(settings_with_placeholder, component="web-admin")


def test_bootstrap_guard_allows_strong_production_config():
    """Корректная конфигурация не порождает ошибок стража."""
    from core.bootstrap_guard import evaluate_config

    report = evaluate_config(_prod_settings())
    assert report.ok, report.errors


def test_startup_guard_only_warns_in_development():
    """В разработке страж ограничивается предупреждениями (не ломает запуск)."""
    from core.startup_guard import enforce_startup_security

    report = enforce_startup_security(
        _prod_settings(
            APP_ENV="development",
            SESSION_SECRET_KEY="change_me_session_secret_change_me_session_secret",
        ),
        component="web-admin",
    )
    assert report.ok, "В разработке ошибки конфигурации не блокируют запуск"
    assert any("Placeholder" in text for text in report.warnings)


@pytest.mark.asyncio
async def test_bootstrap_mode_status_visible_while_no_permanent_superadmin(db_session_maker):
    """bootstrap_mode=True, пока в базе нет постоянного активного суперадмина."""
    from core.bootstrap_guard import bootstrap_mode_status

    bootstrap_username = "bootstrap_admin"
    cfg = _prod_settings(
        WEB_ADMIN_USERNAME=bootstrap_username,
        WEB_ADMIN_PASSWORD="strong-bootstrap-pass",
    )
    async with db_session_maker() as session:
        status = await bootstrap_mode_status(session, cfg)
        assert status["bootstrap_mode"] is True
        assert status["permanent_superadmins"] == 0
        assert status["bootstrap_username"] == bootstrap_username

        session.add(
            WebUser(
                username="permanent_root",
                password_hash=hash_password("permanent_password_123"),
                role=WebRole.SUPERADMIN,
                is_active=True,
            )
        )
        await session.commit()

        status_after = await bootstrap_mode_status(session, cfg)
        assert status_after["bootstrap_mode"] is False
        assert status_after["permanent_superadmins"] == 1


async def test_bootstrap_guard_requires_2fa_channel():
    """Без канала OTP bootstrap-доступ признан неподтверждаемым вторым фактором."""
    from core.bootstrap_guard import evaluate_config, two_factor_channel_ready

    cfg = _prod_settings(
        WEB_ADMIN_USERNAME="bootstrap_admin",
        WEB_ADMIN_PASSWORD="strong-bootstrap-pass",
        WEB_ADMIN_2FA_VK_ID=0,
        VK_REPORT_ADMIN_ID=0,
    )
    assert two_factor_channel_ready(cfg) is False
    report = evaluate_config(cfg)
    assert any("канал доставки 2FA" in text for text in report.warnings)

    cfg_with_channel = _prod_settings(WEB_ADMIN_2FA_VK_ID=987654321)
    assert two_factor_channel_ready(cfg_with_channel) is True


@pytest.mark.asyncio
async def test_health_endpoint_exposes_bootstrap_mode(db_session_maker, monkeypatch):
    """/health обязан показывать bootstrap_mode (временный доступ не должен быть невидим)."""
    monkeypatch.setattr(settings, "WEB_ADMIN_USERNAME", "bootstrap_admin")
    monkeypatch.setattr(settings, "WEB_ADMIN_PASSWORD", "strong-bootstrap-pass")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/health")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ok"
        assert body["bootstrap_mode"] is True
        assert body["bootstrap"]["permanent_superadmins"] == 0


# ==========================================
# Возврат на запрошенную страницу после входа (?next=)
# ==========================================


def test_safe_next_path_rejects_external_hosts():
    """Открытый redirect невозможен: принимаются только внутренние пути."""
    from web.routes.auth import safe_next_path

    assert safe_next_path("/tickets/5") == "/tickets/5"
    assert safe_next_path("/admin/admins/?page=2") == "/admin/admins/?page=2"
    assert safe_next_path("//evil.example.com") is None
    assert safe_next_path("https://evil.example.com") is None
    assert safe_next_path("/") == "/"
    assert safe_next_path(None) is None


@pytest.mark.asyncio
async def test_login_returns_user_to_requested_page(db_session_maker, monkeypatch):
    """После входа пользователя возвращает на страницу, куда он шёл."""
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", False)

    async with db_session_maker() as session:
        session.add(
            WebUser(
                username="next_admin",
                password_hash=hash_password("pass_12345"),
                role=WebRole.DEPARTMENT_ADMIN,
                is_active=True,
            )
        )
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login_page = await client.get("/auth/login?next=/tickets/", follow_redirects=False)
        assert login_page.status_code == 200
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)

        res = await client.post(
            "/auth/login",
            data={"username": "next_admin", "password": "pass_12345", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert res.status_code == 303
        assert res.headers["location"] == "/tickets/"


@pytest.mark.asyncio
async def test_login_ignores_external_next_target(db_session_maker, monkeypatch):
    """Внешний next отклоняется — после входа пользователь остаётся в панели."""
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", False)

    async with db_session_maker() as session:
        session.add(
            WebUser(
                username="next_admin_2",
                password_hash=hash_password("pass_12345"),
                role=WebRole.DEPARTMENT_ADMIN,
                is_active=True,
            )
        )
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login_page = await client.get(
            "/auth/login?next=https://evil.example.com", follow_redirects=False
        )
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)

        res = await client.post(
            "/auth/login",
            data={"username": "next_admin_2", "password": "pass_12345", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert res.status_code == 303
        assert res.headers["location"] == "/"


@pytest.mark.asyncio
async def test_require_auth_preserves_requested_page_in_login_link(db_session_maker):
    """require_auth сохраняет запрошенную страницу в ссылке на вход."""
    from web.routes.auth import safe_next_path

    assert safe_next_path("/tickets/") == "/tickets/"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/tickets/", follow_redirects=False)
        assert res.status_code == 302
        location = res.headers["location"]
        assert location.startswith("/auth/login?next=")
        # Путь сохранён (косая черта не экранируется — safe='/?&=')
        assert location in ("/auth/login?next=/tickets/", "/auth/login?next=%2Ftickets%2F")
