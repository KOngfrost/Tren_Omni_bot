"""Тесты функционала рассылки в VK-боте (bots/vk/handlers/admin.py)."""

from unittest.mock import AsyncMock

import pytest

from bots.vk.bot import vk_bot
from bots.vk.common import BroadcastStates
from bots.vk.handlers.admin import (
    broadcast_confirm_handler,
    broadcast_count_handler,
    broadcast_direct_handler,
    broadcast_start_handler,
    broadcast_text_handler,
)
from core.models import Admin, User, UserRole, WebRole, WebUser


@pytest.fixture
async def setup_vk_test_data(db_session_maker):
    """Подготовка тестовых пользователей и администраторов в базе."""
    async with db_session_maker() as session:
        # 1. Обычный студент
        u_student = User(vk_id=1001, full_name="Студент Обычный")
        # 2. Обычный админ отдела
        u_admin = User(vk_id=1002, full_name="Админ Отдела")
        # 3. Суперадмин
        u_super = User(vk_id=1003, full_name="Главный Админ")
        session.add_all([u_student, u_admin, u_super])
        await session.flush()

        # Назначаем роли
        adm_dept = Admin(user_id=u_admin.id, role=UserRole.ADMIN, department_id=1)
        adm_super = Admin(user_id=u_super.id, role=UserRole.SUPERADMIN)
        session.add_all([adm_dept, adm_super])

        # Веб-пользователь суперадмина
        web_super = WebUser(
            username="super_bot_tester",
            password_hash="fake",
            role=WebRole.SUPERADMIN,
            is_active=True,
            admin_id=adm_super.id,
        )
        session.add(web_super)
        await session.commit()


@pytest.mark.asyncio
async def test_broadcast_start_non_admin_forbidden(setup_vk_test_data):
    """Обычный студент не имеет доступа к команде 'Рассылка'."""
    message = AsyncMock()
    message.from_id = 1001
    message.text = "Рассылка"
    message.answer = AsyncMock()

    await broadcast_start_handler(message)

    message.answer.assert_called_once()
    reply = message.answer.call_args[0][0]
    assert "У вас нет доступа" in reply


@pytest.mark.asyncio
async def test_broadcast_start_dept_admin_forbidden(setup_vk_test_data):
    """Админ отдела не может запускать рассылку (только суперадмин)."""
    message = AsyncMock()
    message.from_id = 1002
    message.text = "Рассылка"
    message.answer = AsyncMock()

    await broadcast_start_handler(message)

    message.answer.assert_called_once()
    reply = message.answer.call_args[0][0]
    assert "доступна только суперадминистраторам" in reply


@pytest.mark.asyncio
async def test_broadcast_full_fsm_flow(setup_vk_test_data, monkeypatch):
    """Полный FSM сценарий рассылки суперадмином: старт -> ввод N -> ввод текста -> подтверждение."""
    monkeypatch.setattr("core.services.broadcast_service.fire_outbox_delivery", lambda: None)

    # 1. Старт
    msg_start = AsyncMock()
    msg_start.from_id = 1003
    msg_start.text = "Рассылка"
    msg_start.answer = AsyncMock()

    await broadcast_start_handler(msg_start)

    msg_start.answer.assert_called_once()
    assert "Массовая рассылка сообщений" in msg_start.answer.call_args[0][0]
    current_state = await vk_bot.state_dispenser.get(1003)
    assert current_state is not None
    assert current_state.state == BroadcastStates.WAITING_COUNT

    # 2. Некорректный ввод числа N
    msg_bad_count = AsyncMock()
    msg_bad_count.from_id = 1003
    msg_bad_count.text = "сто"
    msg_bad_count.answer = AsyncMock()

    await broadcast_count_handler(msg_bad_count)
    assert "положительное целое число" in msg_bad_count.answer.call_args[0][0]

    # 3. Корректный ввод N
    msg_count = AsyncMock()
    msg_count.from_id = 1003
    msg_count.text = "2"
    msg_count.answer = AsyncMock()

    await broadcast_count_handler(msg_count)
    assert "Выбрано 2 последних пользователей" in msg_count.answer.call_args[0][0]
    current_state = await vk_bot.state_dispenser.get(1003)
    assert current_state.state == BroadcastStates.WAITING_TEXT
    assert current_state.payload.get("count") == 2

    # 4. Ввод текста рассылки
    msg_text = AsyncMock()
    msg_text.from_id = 1003
    msg_text.text = "Завтра день донора в клубе общежития!"
    msg_text.answer = AsyncMock()

    await broadcast_text_handler(msg_text)
    assert "Подтверждение рассылки" in msg_text.answer.call_args[0][0]
    current_state = await vk_bot.state_dispenser.get(1003)
    assert current_state.state == BroadcastStates.WAITING_CONFIRM
    assert current_state.payload.get("text") == "Завтра день донора в клубе общежития!"

    # 5. Подтверждение рассылки
    msg_confirm = AsyncMock()
    msg_confirm.from_id = 1003
    msg_confirm.text = "Подтвердить рассылку"
    msg_confirm.answer = AsyncMock()

    await broadcast_confirm_handler(msg_confirm)
    assert "успешно запущена" in msg_confirm.answer.call_args[0][0]
    # Состояние должно быть очищено
    assert await vk_bot.state_dispenser.get(1003) is None


@pytest.mark.asyncio
async def test_broadcast_cancel_flow(setup_vk_test_data):
    """Отмена рассылки на шаге ввода N."""
    await vk_bot.state_dispenser.set(1003, BroadcastStates.WAITING_COUNT)

    msg_cancel = AsyncMock()
    msg_cancel.from_id = 1003
    msg_cancel.text = "Отмена"
    msg_cancel.answer = AsyncMock()

    await broadcast_count_handler(msg_cancel)

    assert "Рассылка отменена" in msg_cancel.answer.call_args[0][0]
    assert await vk_bot.state_dispenser.get(1003) is None


@pytest.mark.asyncio
async def test_broadcast_direct_command(setup_vk_test_data):
    """Быстрая команда: Рассылка 3 Срочная новость."""
    msg = AsyncMock()
    msg.from_id = 1003
    msg.text = "Рассылка 3 Срочное собрание студсовета"
    msg.answer = AsyncMock()

    await broadcast_direct_handler(msg)

    msg.answer.assert_called_once()
    assert "Подтверждение рассылки" in msg.answer.call_args[0][0]
    current_state = await vk_bot.state_dispenser.get(1003)
    assert current_state is not None
    assert current_state.state == BroadcastStates.WAITING_CONFIRM
    assert current_state.payload.get("count") == 3
    assert current_state.payload.get("text") == "Срочное собрание студсовета"
