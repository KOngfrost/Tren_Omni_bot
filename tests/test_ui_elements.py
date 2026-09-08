"""
Блок A. Проверка UI элементов и кнопок (UI/UX Testing).

Проверяет наличие и корректность UI-элементов на страницах веб-панели:
- Кнопки и их CSS-классы (Normal, Hover, Active, Disabled через CSS)
- Формы ввода и поля
- Навигационные ссылки и редиректы
- CSRF-токены в формах

Все проверки выполняются через TestClient + in-memory SQLite.
"""

import os
import re
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def client(monkeypatch):
    """TestClient с in-memory SQLite и подменой async_session_maker во всех модулях."""
    import asyncio

    monkeypatch.setenv("SESSION_SECRET_KEY", "test_secret_key_for_tests_1234567890")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "false")

    import core.database as database_module
    import core.ticket_service as ticket_service_module
    import core.reporting as reporting_module
    import core.outbox as outbox_module
    from core.models import Base
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite://")

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    modules_to_patch = [
        database_module, ticket_service_module, reporting_module, outbox_module,
    ]
    for mod_name in (
        "web.routes.tickets", "web.routes.dashboard", "web.routes.faq",
        "web.routes.events", "web.routes.knowledge_base", "web.routes.departments",
        "web.routes.api", "web.routes.logs", "web.routes.admin_panel",
    ):
        mod = __import__(mod_name, fromlist=["async_session_maker"])
        if hasattr(mod, "async_session_maker"):
            modules_to_patch.append(mod)

    for mod in modules_to_patch:
        monkeypatch.setattr(mod, "async_session_maker", maker, raising=True)

    monkeypatch.setattr(database_module, "engine", engine)

    monkeypatch.setattr("web.routes.auth.settings.WEB_ADMIN_USERNAME", "testadmin")
    monkeypatch.setattr("web.routes.auth.settings.WEB_ADMIN_PASSWORD", "test_password_123")
    monkeypatch.setattr("web.routes.auth.settings.VK_REPORT_ADMIN_ID", 0)

    from web.main import app
    yield TestClient(app, raise_server_exceptions=False)

    asyncio.run(engine.dispose())


def _login(client):
    """Выполнить bootstrap-логин и вернуть cookies."""
    login_page = client.get("/auth/login")
    csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text)
    assert csrf_token, "CSRF token not found on login page"
    token = csrf_token.group(1)
    response = client.post(
        "/auth/login",
        data={"username": "testadmin", "password": "test_password_123", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303, f"Login failed: {response.status_code}"
    return client.cookies


def _get_sid(client):
    """Получить session_id из cookies."""
    for key in client.cookies.keys():
        if "session_" in key:
            return key.replace("session_", "")
    return None


# ==========================================
# A.1 Кнопки и навигация — страница логина
# ==========================================

class TestLoginPageUI:
    """A.1 — Проверка кнопок и форм на странице логина."""

    def test_login_page_contains_login_button(self, client):
        """Кнопка «Войти» присутствует на странице логина."""
        resp = client.get("/auth/login")
        assert resp.status_code == 200
        assert "btn btn-primary btn-full" in resp.text
        assert "Войти" in resp.text

    def test_login_page_contains_username_field(self, client):
        """Поле ввода логина присутствует и является обязательным."""
        resp = client.get("/auth/login")
        assert 'name="username"' in resp.text
        assert "required" in resp.text
        assert "autofocus" in resp.text

    def test_login_page_contains_password_field(self, client):
        """Поле ввода пароля присутствует, тип password."""
        resp = client.get("/auth/login")
        assert 'name="password"' in resp.text
        assert 'type="password"' in resp.text
        assert "required" in resp.text

    def test_login_page_contains_csrf_token(self, client):
        """В форме есть скрытое поле csrf_token."""
        resp = client.get("/auth/login")
        assert 'name="csrf_token"' in resp.text
        assert 'type="hidden"' in resp.text

    def test_login_page_contains_password_toggle_button(self, client):
        """Кнопка переключения видимости пароля присутствует."""
        resp = client.get("/auth/login")
        assert "toggle-password" in resp.text
        assert "aria-label" in resp.text

    def test_login_page_button_hover_css_class(self, client):
        """Кнопка имеет CSS-класс btn-primary (стиль Normal по умолчанию)."""
        resp = client.get("/auth/login")
        assert "btn-primary" in resp.text


# ==========================================
# A.1 Кнопки и навигация — боковая панель
# ==========================================

class TestSidebarNavigation:
    """A.1 — Проверка навигационных ссылок в боковой панели."""

    def test_sidebar_contains_all_nav_links_when_logged_in(self, client):
        """После входа боковая панель содержит все навигационные ссылки."""
        _login(client)
        sid = _get_sid(client)
        assert sid is not None, "Session cookie not found after login"
        resp = client.get(f"/?sid={sid}")
        assert resp.status_code == 200
        assert '/tickets/' in resp.text
        assert '/knowledge/' in resp.text
        assert '/faq/' in resp.text
        assert '/events/' in resp.text
        assert '/logs/' in resp.text

    def test_logout_button_present_in_sidebar(self, client):
        """Кнопка «Выйти» отображается в боковой панели."""
        _login(client)
        sid = _get_sid(client)
        resp = client.get(f"/?sid={sid}")
        assert "btn-logout" in resp.text
        assert "Выйти" in resp.text

    def test_unauthenticated_access_redirects_to_login(self, client):
        """Доступ к защищенной странице без аутентификации перенаправляет на логин."""
        resp = client.get("/tickets/", follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        assert "/auth/login" in resp.headers.get("location", "")

    def test_login_redirect_leads_to_dashboard(self, client):
        """После успешного логина редирект на главную страницу (dashboard)."""
        login_page = client.get("/auth/login")
        csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)
        response = client.post(
            "/auth/login",
            data={"username": "testadmin", "password": "test_password_123", "csrf_token": csrf_token},
            follow_redirects=False,
        )
        assert response.status_code == 303
        location = response.headers.get("location", "")
        assert "sid=" in location
        assert location.startswith("/?sid=")

# ==========================================
# A.1 Кнопки и навигация — страницы модулей
# ==========================================

class TestDashboardButtons:
    """A.1 — Проверка кнопок на странице дашборда."""

    def test_dashboard_contains_stat_cards(self, client):
        """Дашборд содержит карточки статистики."""
        _login(client)
        sid = _get_sid(client)
        resp = client.get(f"/?sid={sid}")
        assert resp.status_code == 200
        assert "stat-card" in resp.text
        assert "Всего заявок" in resp.text
        assert "В обработке" in resp.text
        assert "Выполнено" in resp.text
        assert "Новые" in resp.text

    def test_dashboard_contains_action_buttons(self, client):
        """Дашборд содержит кнопки действий."""
        _login(client)
        sid = _get_sid(client)
        resp = client.get(f"/?sid={sid}")
        assert "btn-secondary" in resp.text  # «Все заявки»
        assert "btn-primary" in resp.text  # «Управление админами»


class TestTicketsPageButtons:
    """A.1 — Проверка кнопок и фильтров на странице заявок."""

    def test_tickets_page_contains_filter_button(self, client):
        """Страница заявок содержит кнопку «Применить» для фильтров."""
        _login(client)
        sid = _get_sid(client)
        resp = client.get(f"/tickets/?sid={sid}")
        assert resp.status_code == 200
        assert "filter-bar" in resp.text
        assert 'type="submit"' in resp.text
        assert "Применить" in resp.text

    def test_tickets_page_contains_search_input(self, client):
        """Страница заявок содержит поле поиска."""
        _login(client)
        sid = _get_sid(client)
        resp = client.get(f"/tickets/?sid={sid}")
        assert 'name="q"' in resp.text
        assert "Поиск" in resp.text

    def test_tickets_page_contains_status_filter(self, client):
        """Страница заявок содержит выпадающий список статусов."""
        _login(client)
        sid = _get_sid(client)
        resp = client.get(f"/tickets/?sid={sid}")
        assert 'name="status"' in resp.text
        assert "Все статусы" in resp.text


class TestFAQPageButtons:
    """A.1 — Проверка кнопок на странице FAQ."""

    def test_faq_page_contains_add_button(self, client):
        """Страница FAQ содержит кнопку «Добавить вопрос»."""
        _login(client)
        sid = _get_sid(client)
        resp = client.get(f"/faq/?sid={sid}")
        assert resp.status_code == 200
        assert "Добавить вопрос" in resp.text
        assert 'data-open-modal="modal"' in resp.text


class TestEventsPageButtons:
    """A.1 — Проверка кнопок на странице событий."""

    def test_events_page_contains_create_button(self, client):
        """Страница событий содержит кнопку «Создать событие»."""
        _login(client)
        sid = _get_sid(client)
        resp = client.get(f"/events/?sid={sid}")
        assert resp.status_code == 200
        assert "Создать событие" in resp.text
        assert 'data-open-modal="modal"' in resp.text


class TestKnowledgeBaseButtons:
    """A.1 — Проверка кнопок на странице базы знаний."""

    def test_kb_page_contains_add_button(self, client):
        """Страница базы знаний содержит кнопку «Добавить запись»."""
        _login(client)
        sid = _get_sid(client)
        resp = client.get(f"/knowledge/?sid={sid}")
        assert resp.status_code == 200
        assert "Добавить запись" in resp.text
        assert 'data-open-modal="modal"' in resp.text


class TestDepartmentsPageButtons:
    """A.1 — Проверка кнопок на странице отделов."""

    def test_departments_page_contains_create_button(self, client):
        """Страница отделов содержит кнопку «Создать отдел»."""
        _login(client)
        sid = _get_sid(client)
        resp = client.get(f"/departments/?sid={sid}")
        assert resp.status_code == 200
        assert "Создать отдел" in resp.text
        assert 'data-open-modal="create-modal"' in resp.text


class TestLogsPageButtons:
    """A.1 — Проверка кнопок на странице логов."""

    def test_logs_page_contains_export_button(self, client):
        """Страница логов содержит кнопку экспорта в CSV."""
        _login(client)
        sid = _get_sid(client)
        resp = client.get(f"/logs/?sid={sid}")
        assert resp.status_code == 200
        assert "Экспорт" in resp.text
        assert 'href="/logs/export"' in resp.text


class TestAdminsPageButtons:
    """A.1 — Проверка кнопок на странице администраторов."""

    def test_admins_page_contains_add_button(self, client):
        """Страница администраторов содержит кнопку «Добавить админа»."""
        _login(client)
        sid = _get_sid(client)
        resp = client.get(f"/admin/admins/?sid={sid}")
        assert resp.status_code == 200
        assert "Добавить админа" in resp.text
        assert 'data-open-modal="modal"' in resp.text


class TestCssButtonStates:
    """A.1 — Проверка CSS-состояний кнопок (Normal, Hover, Active, Disabled."""

    def test_css_contains_hover_state(self, client):
        """Таблица стилей содержит :hover для кнопок."""
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        assert ":hover" in resp.text

    def test_css_contains_active_state(self, client):
        """Таблица стилей содержит :active для кнопок."""
        resp = client.get("/static/style.css")
        assert ":active" in resp.text

    def test_css_contains_disabled_state(self, client):
        """Таблица стилей содержит :disabled для кнопок."""
        resp = client.get("/static/style.css")
        assert ":disabled" in resp.text
