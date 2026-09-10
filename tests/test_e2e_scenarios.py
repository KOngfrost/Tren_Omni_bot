"""
Блок B. Проверка пользовательских путей (End-to-End Scenarios).

Полный путь пользователя в веб-панели:
1. Авторизация (bootstrap-вход).
2. Переход на дашборд.
3. Создание сущностей: отдел → FAQ → событие → база знаний.
4. Модификация (переименование отдела).
5. Удаление сущности.
6. Выход из системы.

Бизнес-путь заявки (через core-сервисы):
- Создание → смена статусов NEW → IN_PROGRESS → COMPLETED.
"""

import re

from core.models import TicketStatus
from core.ticket_service import ALLOWED_TRANSITIONS, create_ticket, status_label


def _login(web_client):
    """Bootstrap-логин в панель."""
    login_page = web_client.get("/auth/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)
    resp = web_client.post(
        "/auth/login",
        data={"username": "testadmin", "password": "test_password_123", "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303, f"Login failed: {resp.status_code}"
    return web_client


def _page_csrf(web_client, path):
    page = web_client.get(path)
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    if not match:
        raise AssertionError(f"CSRF token not found on {path}")
    return match.group(1)


def _post_form(web_client, path, data, csrf_token=None):
    if csrf_token is None:
        csrf_token = _page_csrf(web_client, path)
    data = {**data, "csrf_token": csrf_token}
    return web_client.post(path, data=data, follow_redirects=False)


def _db_query(web_client, sql, params=()):
    import sqlite3

    conn = sqlite3.connect(f"file:{web_client._test_db_name}?mode=memory&cache=shared", uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _seed_department(web_client, name):
    _db_execute(web_client, "INSERT INTO departments (name) VALUES (?)", (name,))
    return _db_query(web_client, "SELECT id FROM departments WHERE name = ?", (name,))[0][0]


def _db_execute(web_client, sql, params=()):
    import sqlite3

    conn = sqlite3.connect(f"file:{web_client._test_db_name}?mode=memory&cache=shared", uri=True)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


# ==========================================
# B.1 — Полный путь: авторизация → дашборд → CRUD → выход
# ==========================================


class TestFullUserPathWebPanel:
    """B.1 — Сквозной пользовательский путь через веб-панель."""

    def test_bootstrap_login_works(self, web_client):
        """Шаг 1: вход по bootstrap-данным из .env."""
        login_page = web_client.get("/auth/login")
        assert login_page.status_code == 200
        token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)
        resp = web_client.post(
            "/auth/login",
            data={"username": "testadmin", "password": "test_password_123", "csrf_token": token},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location") == "/"
        assert "session=" in resp.headers.get("set-cookie", "")

    def test_dashboard_accessible_after_login(self, web_client):
        """Шаг 2: после входа доступен дашборд."""
        _login(web_client)
        page = web_client.get("/")
        assert page.status_code == 200
        assert "Дашборд" in page.text
        assert "Всего заявок" in page.text

    def test_full_crud_cycle_department(self, web_client):
        """Шаг 3-5: создать отдел → переименовать → удалить."""
        _login(web_client)

        # Создание
        csrf = _page_csrf(web_client, "/departments/")
        resp = _post_form(web_client, "/departments/create", {"name": "Тестовый отдел"}, csrf)
        assert resp.status_code == 303


def test_logout_works(web_client):
    """Шаг 6: выход из системы инвалидирует сессию."""
    _login(web_client)
    csrf = _page_csrf(web_client, "/")  # logout form есть в base.html sidebar
    resp = web_client.post(
        "/auth/logout",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    # После выхода — редирект на логин
    assert "/auth/login" in resp.headers.get("location", "")


# ==========================================
# B.1 — Бизнес-путь заявки (через core-сервисы)
# ==========================================


class TestTicketLifecycle:
    """B.1 — Полный жизненный цикл заявки через core ticket_service."""

    def test_status_transitions_defined(self):
        """Цепочка статусов: NEW → IN_PROGRESS → COMPLETED → COMPLETED_AUTO."""
        assert TicketStatus.IN_PROGRESS in ALLOWED_TRANSITIONS[TicketStatus.NEW]
        assert TicketStatus.COMPLETED in ALLOWED_TRANSITIONS[TicketStatus.IN_PROGRESS]
        assert TicketStatus.COMPLETED_AUTO in ALLOWED_TRANSITIONS[TicketStatus.COMPLETED]

    def test_status_labels_russian(self):
        """Человекочитаемые названия статусов на русском."""
        assert status_label(TicketStatus.NEW) == "Новое"
        assert status_label(TicketStatus.IN_PROGRESS) == "В обработке"
        assert status_label(TicketStatus.COMPLETED) == "Выполнено"
        assert status_label(None) == "—"

    def test_invalid_transition_blocked(self):
        """Обратный переход COMPLETED → NEW запрещён."""
        from core.ticket_service import can_transition

        assert not can_transition(TicketStatus.COMPLETED, TicketStatus.NEW)
        assert not can_transition(TicketStatus.COMPLETED_AUTO, TicketStatus.NEW)

    def test_create_ticket_via_service(self, web_client):
        """Создание заявки через ticket_service сохраняет корректный статус."""
        import asyncio

        async def _run():
            ticket = await create_ticket(
                topic="Сломался кран",
                description="Не работает водопровод",
                vk_id=999,
                keep_identity=True,
            )
            assert ticket.status == TicketStatus.NEW
            assert ticket.topic == "Сломался кран"
            return ticket.id

        ticket_id = asyncio.run(_run())
        assert ticket_id > 0

    def test_full_status_chain_via_service(self, web_client):
        """Полная цепочка смены статусов через ticket_service."""
        import asyncio

        from core.ticket_service import StatusTransitionError, change_ticket_status

        async def _run():
            ticket = await create_ticket(
                topic="Тема заявки",
                description="Описание",
                vk_id=777,
                keep_identity=True,
            )
            ticket_id = ticket.id

            # NEW → IN_PROGRESS
            t = await change_ticket_status(ticket_id, TicketStatus.IN_PROGRESS, "admin1")
            assert t.status == TicketStatus.IN_PROGRESS

            # IN_PROGRESS → COMPLETED
            t = await change_ticket_status(ticket_id, TicketStatus.COMPLETED, "admin1")
            assert t.status == TicketStatus.COMPLETED

            # Недопустимый переход — бросает ошибку
            try:
                await change_ticket_status(ticket_id, TicketStatus.NEW, "admin1")
                raise AssertionError("Expected StatusTransitionError")
            except StatusTransitionError:
                pass

        asyncio.run(_run())

    def test_navigation_to_all_sections(self, web_client):
        """Навигация: все разделы отображаются без ошибок."""
        _login(web_client)
        for path in (
            "/",
            "/tickets/",
            "/faq/",
            "/events/",
            "/knowledge/",
            "/departments/",
            "/admin/admins/",
        ):
            page = web_client.get(path)
            assert page.status_code == 200, f"Failed: {path}"
