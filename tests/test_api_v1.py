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


def _build_telegram_init_data(bot_token: str, auth_date: int, user_json: str) -> str:
    import hashlib
    import hmac
    from urllib.parse import quote

    vals = {
        "auth_date": str(auth_date),
        "user": user_json,
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(vals.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return f"auth_date={auth_date}&user={quote(user_json)}&hash={computed_hash}"


def test_telegram_init_data_replay_attack_rejected():
    """Telegram initData старше 24 часов должно отклоняться с 401 'Init data expired'."""
    import time

    from fastapi import HTTPException

    from web.routes.api_v1 import _validate_telegram_init_data

    bot_token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    # auth_date 2 дня назад (> 86400 секунд)
    expired_time = int(time.time()) - 100000
    expired_init_data = _build_telegram_init_data(
        bot_token, expired_time, '{"id": 999, "first_name": "Test"}'
    )

    with pytest.raises(HTTPException) as exc_info:
        _validate_telegram_init_data(expired_init_data, bot_token)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Init data expired"


def test_telegram_init_data_fresh_accepted():
    """Свежие Telegram initData должны успешно проходить валидацию."""
    import time

    from web.routes.api_v1 import _validate_telegram_init_data

    bot_token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    fresh_time = int(time.time()) - 30
    fresh_init_data = _build_telegram_init_data(
        bot_token, fresh_time, '{"id": 999, "first_name": "Test"}'
    )

    validated = _validate_telegram_init_data(fresh_init_data, bot_token)
    assert validated is not None
    assert int(validated["auth_date"]) == fresh_time
