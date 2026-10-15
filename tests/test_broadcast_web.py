"""Интеграционные тесты веб-маршрутов рассылки (/broadcast/)."""

import re

import pytest
from fastapi.testclient import TestClient

from core.models import Admin, User, WebRole, WebUser
from web.security.passwords import hash_password


def _extract_csrf(html_text: str) -> str:
    m = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', html_text)
    if not m:
        m = re.search(r'value=["\']([^"\']+)["\']\s+name=["\']csrf_token["\']', html_text)
    return m.group(1) if m else "mock_csrf"


@pytest.fixture
async def setup_users_and_admins(db_session_maker):
    """Создаёт тестовых пользователей, суперадмина и админа отдела."""
    async with db_session_maker() as session:
        # Студенты
        u1 = User(vk_id=501, full_name="Студент 1", dormitory="№16")
        u2 = User(vk_id=502, full_name="Студент 2", dormitory="№15")
        u3 = User(vk_id=503, full_name="Студент 3", dormitory="№16")
        session.add_all([u1, u2, u3])
        await session.flush()

        # Суперадмин
        sa_admin = Admin(user_id=u1.id, role="SUPERADMIN")
        session.add(sa_admin)
        await session.flush()
        sa_web = WebUser(
            username="super_broadcaster",
            password_hash=hash_password("SuperSecretPass123"),
            role=WebRole.SUPERADMIN,
            is_active=True,
            admin_id=sa_admin.id,
        )
        session.add(sa_web)

        # Обычный админ отдела
        dept_admin = Admin(user_id=u2.id, role="ADMIN")
        session.add(dept_admin)
        await session.flush()
        dept_web = WebUser(
            username="dept_admin_user",
            password_hash=hash_password("DeptSecretPass123"),
            role=WebRole.DEPARTMENT_ADMIN,
            is_active=True,
            admin_id=dept_admin.id,
        )
        session.add(dept_web)

        await session.commit()


def test_broadcast_unauthorized_redirect(web_client: TestClient):
    """Неавторизованный пользователь перенаправляется на логин."""
    resp = web_client.get("/broadcast/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["location"]


def test_broadcast_department_admin_denied(web_client: TestClient, setup_users_and_admins):
    """Админ отдела не имеет доступа к рассылке (только суперадмин)."""
    # Логин админа отдела
    login_page = web_client.get("/auth/login")
    csrf = _extract_csrf(login_page.text)
    res = web_client.post(
        "/auth/login",
        data={"username": "dept_admin_user", "password": "DeptSecretPass123", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert res.status_code == 200

    # Попытка открыть /broadcast/
    bc_resp = web_client.get("/broadcast/", follow_redirects=False)
    assert bc_resp.status_code == 303
    assert bc_resp.headers["location"] == "/"


def test_broadcast_superadmin_flow(web_client: TestClient, setup_users_and_admins, monkeypatch):
    """Суперадмин успешно открывает страницу, смотрит предпросмотр и запускает рассылку."""
    fired = []
    monkeypatch.setattr(
        "core.services.broadcast_service.fire_outbox_delivery", lambda: fired.append(True)
    )

    # 1. Логин суперадмина
    login_page = web_client.get("/auth/login")
    csrf = _extract_csrf(login_page.text)
    res = web_client.post(
        "/auth/login",
        data={
            "username": "super_broadcaster",
            "password": "SuperSecretPass123",
            "csrf_token": csrf,
        },
        follow_redirects=True,
    )
    assert res.status_code == 200

    # 2. Страница рассылки
    bc_page = web_client.get("/broadcast/")
    assert bc_page.status_code == 200
    assert "Рассылка сообщений" in bc_page.text
    assert "Количество последних пользователей" in bc_page.text

    # 3. AJAX предпросмотр
    prev_resp = web_client.get("/broadcast/preview?count=2")
    assert prev_resp.status_code == 200
    prev_data = prev_resp.json()
    assert prev_data["count"] == 2
    assert len(prev_data["users"]) == 2

    # 4. Валидация: пустой текст
    form_csrf = _extract_csrf(bc_page.text)
    err_resp = web_client.post(
        "/broadcast/send",
        data={"count": "2", "text": "   ", "csrf_token": form_csrf},
        follow_redirects=True,
    )
    assert err_resp.status_code == 200
    assert "Текст рассылки не может быть пустым" in err_resp.text

    # 5. Успешная отправка
    send_resp = web_client.post(
        "/broadcast/send",
        data={"count": "2", "text": "Важное объявление!", "csrf_token": form_csrf},
        follow_redirects=True,
    )
    assert send_resp.status_code == 200
    assert "Рассылка успешно сформирована" in send_resp.text
    assert len(fired) >= 1
