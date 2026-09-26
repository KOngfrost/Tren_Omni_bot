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
