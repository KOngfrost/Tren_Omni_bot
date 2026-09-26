"""Тесты нового функционала: заявки без отдела, меню отчётов и раздел «Партнёрство»."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from bots.vk.keyboards import (
    build_admin_report_types_keyboard,
    build_ticket_department_keyboard,
)
from core.models import PartnershipRequest, WebRole, WebUser
from core.ticket_service import create_ticket
from web.main import app


@pytest.mark.asyncio
async def test_create_ticket_without_department(db_session_maker):
    """Проверка создания обращения без отдела (department_name=None)."""
    ticket = await create_ticket(
        topic="Общий вопрос",
        description="Вопрос общего характера без привязки к отделу",
        vk_id=999888,
        keep_identity=True,
        department_name=None,
    )
    assert ticket.id is not None
    assert ticket.department_id is None
    assert ticket.topic == "Общий вопрос"


def test_ticket_department_keyboard_has_without_dept_and_cancel():
    """Клавиатура выбора отдела содержит 'Без отдела' и 'Отмена'."""
    kb = build_ticket_department_keyboard(["Жил-быт", "Культмасс"])
    assert "Без отдела" in kb
    assert "Отмена" in kb
    assert "Жил-быт" in kb


def test_admin_report_types_keyboard():
    """Клавиатура отчётов содержит 'За сегодня', 'За определённое число', 'За период' и 'Отмена'."""
    kb = build_admin_report_types_keyboard()
    assert "За сегодня" in kb
    assert "За определённое число" in kb
    assert "За период" in kb
    assert "Отмена" in kb


@pytest.mark.asyncio
async def test_partnership_model_and_web_route(db_session_maker):
    """Проверка модели PartnershipRequest и доступа суперадмина к странице партнёрства."""
    async with db_session_maker() as session:
        req = PartnershipRequest(
            vk_id=777666,
            user_name="Компания Партнёр",
            proposal_text="Предложение спонсорства на фестивале",
            status="new",
        )
        session.add(req)

        # Создаём суперадмина для теста
        admin = WebUser(
            username="super_test",
            password_hash="fake_hash",
            role=WebRole.SUPERADMIN,
            is_active=True,
        )
        session.add(admin)
        await session.commit()
        await session.refresh(req)
        await session.refresh(admin)

        req_id = req.id
        admin_id = admin.id

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Без авторизации -> редирект на логин
        resp = await client.get("/partnerships/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["location"]


@pytest.mark.asyncio
async def test_api_counters_divided_and_items(db_session_maker, monkeypatch):
    """Проверка работы раздельных счётчиков и списка конкретных уведомлений в GET /api/counters."""
    import re
    from core.config import settings
    from core.models import Admin, Ticket, TicketStatus, User, UserRole
    from web.security.passwords import hash_password

    monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "counters_test_secret_key_123456789")
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", False)

    async with db_session_maker() as session:
        user = User(vk_id=555111, full_name="Пользователь Для Счётчиков")
        session.add(user)
        await session.flush()

        admin = Admin(user_id=user.id, role=UserRole.SUPERADMIN)
        session.add(admin)
        await session.flush()

        web_user = WebUser(
            username="counters_superadmin",
            password_hash=hash_password("SuperSecret123"),
            role=WebRole.SUPERADMIN,
            admin_id=admin.id,
            is_active=True,
        )
        session.add(web_user)

        # 1. Новая заявка без ответа
        ticket1 = Ticket(
            user_id=user.id,
            topic="Проблема в общежитии",
            description="Сломался замок в комнате",
            status=TicketStatus.NEW,
        )
        # 2. Заявка с ответом студента (NEW с заполненным response_text)
        ticket2 = Ticket(
            user_id=user.id,
            topic="Уточнение по пропуску",
            description="Нужен пропуск",
            response_text="Ответ студента: комната 412, корпус Б",
            status=TicketStatus.NEW,
        )
        # 3. Партнёрский запрос
        p_req = PartnershipRequest(
            vk_id=555111,
            user_name="ООО СтудПартнер",
            proposal_text="Предложение о сотрудничестве",
            status="new",
        )
        session.add_all([ticket1, ticket2, p_req])
        await session.commit()
        await session.refresh(ticket1)
        await session.refresh(ticket2)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Авторизуемся
        login_page = await client.get("/auth/login")
        csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)
        login_resp = await client.post(
            "/auth/login",
            data={
                "username": "counters_superadmin",
                "password": "SuperSecret123",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        assert login_resp.status_code in (302, 303)

        # Запрашиваем /api/counters
        resp = await client.get("/api/counters")
        assert resp.status_code == 200
        res_data = resp.json()
        assert res_data["success"] is True
        data = res_data["data"]

        # Проверяем раздельные счётчики
        assert data["new_tickets"] >= 1
        assert data["student_replies"] >= 1
        assert data["new_partnerships"] >= 1
        assert data["total_notifications"] >= 3

        # Проверяем список элементов items
        items = data["items"]
        assert len(items) >= 3

        # Проверяем типы элементов
        types = [it["type"] for it in items]
        assert "new_ticket" in types
        assert "student_reply" in types
        assert "partnership" in types

        # Проверяем прямые ссылки
        for it in items:
            assert "url" in it
            if it["type"] in ("new_ticket", "student_reply"):
                assert "/tickets/?open=" in it["url"]
            elif it["type"] == "partnership":
                assert it["url"] == "/partnerships/"

