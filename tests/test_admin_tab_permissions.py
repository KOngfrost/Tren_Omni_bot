"""Тесты гранулярных прав доступа к вкладкам веб-панели (партнёрство, рассылка, логи)."""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from core.models import Admin, Log, User, WebRole, WebUser
from web.security.passwords import hash_password


def _extract_csrf(html_text: str) -> str:
    m = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', html_text)
    if not m:
        m = re.search(r'value=["\']([^"\']+)["\']\s+name=["\']csrf_token["\']', html_text)
    return m.group(1) if m else "mock_csrf"


@pytest.fixture
async def setup_test_users(db_session_maker):
    """Создаёт суперадмина и обычного админа отдела для проверки прав."""
    async with db_session_maker() as session:
        u_sa = User(vk_id=601, full_name="Главный Админ")
        u_dept = User(vk_id=602, full_name="Админ Отдела")
        session.add_all([u_sa, u_dept])
        await session.flush()

        sa_admin = Admin(user_id=u_sa.id, role="superadmin")
        session.add(sa_admin)
        await session.flush()

        sa_web = WebUser(
            username="super_tab_admin",
            password_hash=hash_password("SuperSecretPass123!"),
            role=WebRole.SUPERADMIN,
            is_active=True,
            admin_id=sa_admin.id,
        )
        session.add(sa_web)

        dept_admin = Admin(user_id=u_dept.id, role="admin")
        session.add(dept_admin)
        await session.flush()

        dept_web = WebUser(
            username="regular_dept_admin",
            password_hash=hash_password("DeptSecretPass123!"),
            role=WebRole.DEPARTMENT_ADMIN,
            is_active=True,
            admin_id=dept_admin.id,
            can_access_partnerships=False,
            can_access_broadcast=False,
            can_access_logs=False,
        )
        session.add(dept_web)

        await session.commit()
        return {"sa_web_id": sa_web.id, "dept_web_id": dept_web.id}


def _login(client: TestClient, username: str, password: str):
    login_page = client.get("/auth/login")
    csrf = _extract_csrf(login_page.text)
    client.post(
        "/auth/login",
        data={"username": username, "password": password, "csrf_token": csrf},
        follow_redirects=True,
    )


def test_default_regular_admin_denied_from_all_three_tabs(
    web_client: TestClient, setup_test_users
):
    """По умолчанию обычный админ не имеет доступа ни к одной из трёх вкладок."""
    _login(web_client, "regular_dept_admin", "DeptSecretPass123!")

    # 1. Партнёрство: 403
    resp_part = web_client.get("/partnerships/")
    assert resp_part.status_code == 403

    # 2. Логи: 403
    resp_logs = web_client.get("/logs/")
    assert resp_logs.status_code == 403

    # 3. Рассылка: редирект на главную страницу с ошибкой доступа
    resp_bc = web_client.get("/broadcast/", follow_redirects=False)
    assert resp_bc.status_code == 303
    assert resp_bc.headers["location"] == "/"


def test_superadmin_can_assign_tab_permissions(
    web_client: TestClient, setup_test_users, db_session_maker
):
    """Суперадмин может назначить доступ к партнёрству и логам, после чего админ сразу получает доступ."""
    dept_id = setup_test_users["dept_web_id"]

    # Входим как суперадмин
    _login(web_client, "super_tab_admin", "SuperSecretPass123!")

    # Получаем CSRF со страницы администраторов
    admins_page = web_client.get("/admin/admins/")
    assert admins_page.status_code == 200
    csrf = _extract_csrf(admins_page.text)

    # Назначаем права: партнёрство и логи (без рассылки)
    post_resp = web_client.post(
        f"/admin/admins/web-users/{dept_id}/permissions",
        data={
            "csrf_token": csrf,
            "can_access_partnerships": "1",
            "can_access_logs": "1",
            # can_access_broadcast не передаётся
        },
        follow_redirects=True,
    )
    assert post_resp.status_code == 200
    assert "успешно сохранены" in post_resp.text

    # Выходим и входим как обычный админ
    web_client.post("/auth/logout", data={"csrf_token": csrf}, follow_redirects=True)
    _login(web_client, "regular_dept_admin", "DeptSecretPass123!")

    # 1. Партнёрство теперь доступно (200 OK)
    resp_part = web_client.get("/partnerships/")
    assert resp_part.status_code == 200
    assert "Партнёрство" in resp_part.text

    # 2. Логи теперь доступны (200 OK)
    resp_logs = web_client.get("/logs/")
    assert resp_logs.status_code == 200
    assert "Журнал" in resp_logs.text

    # 3. Рассылка всё ещё запрещена
    resp_bc = web_client.get("/broadcast/", follow_redirects=False)
    assert resp_bc.status_code == 303


def test_non_superadmin_cannot_edit_permissions(web_client: TestClient, setup_test_users):
    """Обычный администратор не может вызывать эндпоинт изменения прав (защита от горизонтальной эскалации)."""
    dept_id = setup_test_users["dept_web_id"]

    # Входим как обычный админ
    _login(web_client, "regular_dept_admin", "DeptSecretPass123!")

    page = web_client.get("/")
    csrf = _extract_csrf(page.text)

    # Пытаемся назначить себе права на рассылку
    resp = web_client.post(
        f"/admin/admins/web-users/{dept_id}/permissions",
        data={
            "csrf_token": csrf,
            "can_access_broadcast": "1",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Только суперадмин может изменять права доступа" in resp.text

    # Проверяем, что рассылка не открылась
    resp_bc = web_client.get("/broadcast/", follow_redirects=False)
    assert resp_bc.status_code == 303


def test_audit_log_on_permission_change(
    web_client: TestClient, setup_test_users, db_session_maker
):
    """При изменении прав создаётся запись в системном журнале аудита."""
    dept_id = setup_test_users["dept_web_id"]

    _login(web_client, "super_tab_admin", "SuperSecretPass123!")
    admins_page = web_client.get("/admin/admins/")
    csrf = _extract_csrf(admins_page.text)

    web_client.post(
        f"/admin/admins/web-users/{dept_id}/permissions",
        data={
            "csrf_token": csrf,
            "can_access_broadcast": "1",
        },
        follow_redirects=True,
    )

    import asyncio

    async def _check_log():
        async with db_session_maker() as session:
            stmt = (
                select(Log)
                .where(Log.action == "permissions_updated")
                .order_by(Log.created_at.desc())
            )
            entry = await session.scalar(stmt)
            assert entry is not None
            assert f"id={dept_id}" in entry.details
            assert "broadcast=True" in entry.details

    asyncio.run(_check_log())
