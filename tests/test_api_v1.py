"""Тесты для REST API v1 и конечной точки метрик Prometheus."""

import pytest
from httpx import ASGITransport, AsyncClient

from core.models import Department, Ticket, TicketStatus, WebRole, WebUser
from web.main import app
from web.security.passwords import hash_password


@pytest.mark.asyncio
async def test_api_v1_departments_crud(db_session_maker):
    """Проверка CRUD-операций с отделами в API v1."""
    async with db_session_maker() as session:
        admin = WebUser(
            username="v1_admin",
            password_hash=hash_password("admin_pass_v1"),
            role=WebRole.SUPERADMIN,
            is_active=True,
        )
        session.add(admin)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        import re

        login_page = await client.get("/auth/login")
        csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text)
        assert csrf_match, "CSRF token not found"
        csrf = csrf_match.group(1)

        # Авторизация
        login_res = await client.post(
            "/auth/login",
            data={"username": "v1_admin", "password": "admin_pass_v1", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert login_res.status_code in (302, 303)

        # Получаем новый CSRF-токен сессии после входа
        dept_page = await client.get("/departments/")
        post_csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', dept_page.text)
        assert post_csrf_match, "Post-login CSRF token not found"
        csrf = post_csrf_match.group(1)
        csrf_headers = {"X-CSRF-Token": csrf}

        # 1. Получение пустого списка отделов
        get_res = await client.get("/api/v1/departments/")
        assert get_res.status_code == 200
        assert isinstance(get_res.json(), list)

        # 2. Создание отдела через POST
        create_res = await client.post(
            "/api/v1/departments/",
            json={"name": "Новый тестовый отдел API v1"},
            headers=csrf_headers,
        )
        assert create_res.status_code == 201
        data = create_res.json()
        dept_id = data["id"]
        assert data["name"] == "Новый тестовый отдел API v1"

        # 3. Переименование через PUT
        rename_res = await client.put(
            f"/api/v1/departments/{dept_id}",
            json={"name": "Переименованный отдел v1"},
            headers=csrf_headers,
        )
        assert rename_res.status_code == 200
        assert rename_res.json()["name"] == "Переименованный отдел v1"

        # 4. Удаление через DELETE
        delete_res = await client.delete(
            f"/api/v1/departments/{dept_id}",
            headers=csrf_headers,
        )
        assert delete_res.status_code == 200
        assert delete_res.json()["deleted"] is True


@pytest.mark.asyncio
async def test_api_v1_stats_and_tickets(db_session_maker):
    """Проверка эндпоинта статистики и тикетов в API v1."""
    async with db_session_maker() as session:
        admin = WebUser(
            username="v1_stats_admin",
            password_hash=hash_password("admin_pass_stats"),
            role=WebRole.SUPERADMIN,
            is_active=True,
        )
        dept = Department(name="Отдел статистики")
        session.add_all([admin, dept])
        await session.flush()

        ticket = Ticket(
            topic="Вопрос о стипендии",
            description="Когда придёт выплата?",
            status=TicketStatus.NEW,
            department_id=dept.id,
        )
        session.add(ticket)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        import re

        login_page = await client.get("/auth/login")
        csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text)
        assert csrf_match, "CSRF token not found"
        csrf = csrf_match.group(1)

        login_res = await client.post(
            "/auth/login",
            data={
                "username": "v1_stats_admin",
                "password": "admin_pass_stats",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        assert login_res.status_code in (302, 303)

        # Проверка статистики
        stats_res = await client.get("/api/v1/stats")
        assert stats_res.status_code == 200
        stats = stats_res.json()
        assert "version" in stats
        assert stats["total_tickets"] >= 1

        # Проверка списка тикетов
        tickets_res = await client.get("/api/v1/tickets/")
        assert tickets_res.status_code == 200
        tickets = tickets_res.json()
        assert len(tickets) >= 1
        assert tickets[0]["topic"] == "Вопрос о стипендии"


@pytest.mark.asyncio
async def test_prometheus_metrics_endpoint():
    """Проверка доступности эндпоинта Prometheus /metrics."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/metrics")
        assert res.status_code == 200
        assert (
            "http_requests" in res.text or "process_cpu" in res.text or "python_info" in res.text
        )
