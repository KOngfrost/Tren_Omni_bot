"""Комплексные тесты безопасности и защиты от инъекций для VK-бота.

Проверяет:
1. Защиту от SQL-инъекций в темах, описаниях, ответах и фильтрах (SQLi).
2. Защиту от XSS и HTML-инъекций в обращениях и сообщениях.
3. Устойчивость к экстремально большим нагрузкам и buffer overflow (>50k символов).
4. Обработку null-байтов (\x00) в сообщениях.
5. Изоляцию прав и защиту от IDOR / межведомственного несанкционированного доступа (BOLA).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import func, select

from bots.vk.bot import (
    admin_status_handler,
    report_by_date_input,
    report_by_period_input,
    vk_bot,
)
from bots.vk.common import ReportStates, _operator_can_access
from core.models import Admin, Department, Ticket, TicketStatus, User, UserRole
from core.ticket_service import (
    add_student_reply,
    create_ticket,
    reply_to_ticket,
)


@pytest.mark.asyncio
async def test_sqli_in_ticket_description_and_topic(db_session_maker):
    """Проверка устойчивости создания обращений к SQL-инъекциям."""
    sqli_payloads = [
        "' OR 1=1; DROP TABLE tickets; --",
        "'; SELECT * FROM users WHERE '1'='1",
        "1' UNION ALL SELECT NULL, NULL, NULL, NULL--",
        "admin'--",
        '" OR ""="',
        "'; EXEC xp_cmdshell('dir'); --",
    ]

    for payload in sqli_payloads:
        ticket = await create_ticket(
            topic=payload,
            description=f"Description containing injection: {payload}",
            vk_id=999001,
            keep_identity=True,
        )
        assert ticket is not None
        assert ticket.id is not None

        # Проверяем, что таблицы целы и данные сохранились буквально
        async with db_session_maker() as session:
            db_ticket = await session.get(Ticket, ticket.id)
            assert db_ticket is not None
            assert db_ticket.topic == payload
            assert payload in db_ticket.description


@pytest.mark.asyncio
async def test_sqli_in_replies_and_status(db_session_maker):
    """Проверка устойчивости ответов и смены статусов к SQL-инъекциям."""
    async with db_session_maker() as session:
        dept = Department(name="Безопасность")
        user = User(vk_id=888001)
        admin_user = User(vk_id=888002)
        session.add_all([dept, user, admin_user])
        await session.flush()

        admin = Admin(user_id=admin_user.id, role=UserRole.SUPERADMIN)
        ticket = Ticket(
            user_id=user.id,
            department_id=dept.id,
            topic="Нормальная тема",
            description="Обычное описание",
            status=TicketStatus.NEW,
        )
        session.add_all([admin, ticket])
        await session.commit()
        ticket_id = ticket.id

    sqli_payload = "') UNION SELECT user, password FROM users;--"

    # Ответ от администратора
    t_after_admin, _ = await reply_to_ticket(
        ticket_id=ticket_id,
        admin_username="admin_sec",
        message=sqli_payload,
    )
    assert t_after_admin is not None

    # Ответ от студента
    ticket_obj = await add_student_reply(ticket_id=ticket_id, vk_id=888001, message=sqli_payload)
    assert ticket_obj is not None

    # Проверяем целостность БД
    async with db_session_maker() as session:
        count = await session.scalar(select(func.count(Ticket.id)))
        assert count >= 1


@pytest.mark.asyncio
async def test_xss_html_injection_in_ticket(db_session_maker):
    """Проверка, что XSS/HTML теги не исполняются и корректно экранируются/сохраняются."""
    xss_payload = '<script>alert("XSS")</script><img src="x" onerror="alert(1)">'

    ticket = await create_ticket(
        topic="XSS Test",
        description=xss_payload,
        vk_id=777001,
        keep_identity=True,
    )
    assert ticket is not None

    async with db_session_maker() as session:
        db_ticket = await session.get(Ticket, ticket.id)
        assert db_ticket is not None
        # Проверяем, что payload сохранен безопасно в виде строки без выполнения
        assert "<script>" in db_ticket.description


@pytest.mark.asyncio
async def test_buffer_overflow_large_payload(db_session_maker):
    """Проверка устойчивости бота к огромным объемам данных (>50 000 символов)."""
    large_text = "A" * 65536

    # Создание заявки с гигантским описанием
    ticket = await create_ticket(
        topic="Big Payload",
        description=large_text,
        vk_id=666001,
        keep_identity=True,
    )
    assert ticket is not None
    assert ticket.id is not None

    async with db_session_maker() as session:
        db_ticket = await session.get(Ticket, ticket.id)
        assert db_ticket is not None
        assert len(db_ticket.description) == 65536


@pytest.mark.asyncio
async def test_null_byte_injection(db_session_maker):
    """Проверка обработки null-байтов (\x00) в текстовых полях."""
    null_byte_payload = "Prefix\x00Suffix' OR '1'='1"

    ticket = await create_ticket(
        topic="NullByte",
        description=null_byte_payload,
        vk_id=555001,
        keep_identity=True,
    )
    assert ticket is not None

    async with db_session_maker() as session:
        db_ticket = await session.get(Ticket, ticket.id)
        assert db_ticket is not None


@pytest.mark.asyncio
async def test_cross_department_idor_protection(db_session_maker):
    """Проверка изоляции отделов: оператор отдела А не имеет доступа к заявке отдела Б."""
    async with db_session_maker() as session:
        dept_a = Department(name="Отдел А")
        dept_b = Department(name="Отдел Б")
        session.add_all([dept_a, dept_b])
        await session.flush()

        # Оператор отдела А
        op_a_user = User(vk_id=444001)
        # Обычный студент
        student = User(vk_id=444002)
        session.add_all([op_a_user, student])
        await session.flush()

        op_a_admin = Admin(user_id=op_a_user.id, role=UserRole.ADMIN, department_id=dept_a.id)
        # Заявка в отдел Б
        ticket_b = Ticket(
            user_id=student.id,
            department_id=dept_b.id,
            topic="Вопрос в отдел Б",
            description="Секретная информация отдела Б",
            status=TicketStatus.NEW,
        )
        session.add_all([op_a_admin, ticket_b])
        await session.commit()
        ticket_b_id = ticket_b.id

    # Проверяем _operator_can_access: оператор А НЕ имеет доступа к заявке отдела Б
    has_access = await _operator_can_access(vk_id=444001, ticket_id=ticket_b_id)
    assert has_access is False

    # Проверяем, что хендлер смены статуса отклоняет несанкционированное изменение
    msg = MagicMock()
    msg.from_id = 444001
    msg.text = f"Статус #{ticket_b_id}: Выполнено"
    msg.answer = AsyncMock()

    await admin_status_handler(msg)
    msg.answer.assert_called_once()
    assert "Заявка не найдена или недоступна" in msg.answer.call_args[0][0]

    # Проверяем, что статус заявки в БД остался NEW
    async with db_session_maker() as session:
        db_ticket = await session.get(Ticket, ticket_b_id)
        assert db_ticket.status == TicketStatus.NEW


@pytest.mark.asyncio
async def test_reports_date_injection(db_session_maker):
    """Проверка, что некорректные даты или SQL-инъекции в датах отчётов безопасно отвергаются."""
    admin_vk_id = 333001
    async with db_session_maker() as session:
        user = User(vk_id=admin_vk_id)
        session.add(user)
        await session.flush()
        admin = Admin(user_id=user.id, role=UserRole.SUPERADMIN)
        session.add(admin)
        await session.commit()

    # 1. Попытка инъекции в отчет по дате
    msg_sqli_date = MagicMock()
    msg_sqli_date.from_id = admin_vk_id
    msg_sqli_date.text = "31.08.2026'; DROP TABLE tickets;--"
    msg_sqli_date.answer = AsyncMock()

    # Устанавливаем FSM
    await vk_bot.state_dispenser.set(admin_vk_id, ReportStates.WAITING_DATE)
    await report_by_date_input(msg_sqli_date)
    msg_sqli_date.answer.assert_called_once()
    assert "Неверный формат" in msg_sqli_date.answer.call_args[0][0]

    # 2. Попытка инъекции в отчет за период
    msg_sqli_period = MagicMock()
    msg_sqli_period.from_id = admin_vk_id
    msg_sqli_period.text = "01.08.2026 - 31.08.2026' OR 1=1--"
    msg_sqli_period.answer = AsyncMock()

    await vk_bot.state_dispenser.set(admin_vk_id, ReportStates.WAITING_DATE_FROM)
    await report_by_period_input(msg_sqli_period)
    msg_sqli_period.answer.assert_called_once()
    assert "Неверный формат" in msg_sqli_period.answer.call_args[0][0]
