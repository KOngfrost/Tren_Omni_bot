"""Тесты сервиса заявок: переходы статусов, форматирование, маскировка."""

from datetime import datetime

import pytest
from sqlalchemy import select

from core.models import Department, MessageAuthorType, Ticket, TicketMessage, TicketStatus, User
from core.ticket_service import (
    StatusTransitionError,
    _match_department_in_memory,
    can_transition,
    format_ticket_details,
    format_ticket_list,
    mask_anonymous_data,
    validate_transition,
)


def test_transition_new_to_in_progress_allowed():
    assert can_transition(TicketStatus.NEW, TicketStatus.IN_PROGRESS)


def test_transition_new_to_completed_allowed():
    assert can_transition(TicketStatus.NEW, TicketStatus.COMPLETED)


def test_transition_completed_to_new_forbidden():
    assert not can_transition(TicketStatus.COMPLETED, TicketStatus.NEW)


def test_transition_completed_to_auto_allowed():
    assert can_transition(TicketStatus.COMPLETED, TicketStatus.COMPLETED_AUTO)


def test_transition_auto_can_reopen():
    # При ответе студента статус COMPLETED_AUTO возвращается в IN_PROGRESS
    assert can_transition(TicketStatus.COMPLETED_AUTO, TicketStatus.IN_PROGRESS)
    assert not can_transition(TicketStatus.COMPLETED_AUTO, TicketStatus.NEW)


def test_validate_transition_raises():
    with pytest.raises(StatusTransitionError):
        validate_transition(TicketStatus.COMPLETED, TicketStatus.NEW)


def test_validate_transition_same_status_ok():
    # Тот же статус — не ошибка (повторное сохранение)
    validate_transition(TicketStatus.IN_PROGRESS, TicketStatus.IN_PROGRESS)


def test_format_ticket_list_empty():
    assert "нет заявок" in format_ticket_list([]).lower()


def test_format_ticket_list_contains_ticket_info():
    ticket = Ticket(
        id=12,
        topic="Не работает свет",
        status=TicketStatus.IN_PROGRESS,
        response_text="Уже выехали",
    )
    ticket.department = Department(name="Жилбыт")
    text = format_ticket_list([ticket])
    assert "#12" in text
    assert "Жилбыт" in text
    assert "Не работает свет" in text
    assert "В обработке" in text
    assert "Уже выехали" in text


def test_format_ticket_details_with_history():
    ticket = Ticket(id=7, topic="Тема", description="Описание", status=TicketStatus.NEW)
    ticket.department = Department(name="Информ")
    messages = [
        TicketMessage(
            ticket_id=7,
            author_type=MessageAuthorType.USER,
            message="Вопрос от студента",
        ),
        TicketMessage(
            ticket_id=7,
            author_type=MessageAuthorType.ADMIN,
            message="Ответ администратора",
        ),
    ]
    text = format_ticket_details(ticket, messages)
    assert "Заявка #7" in text
    assert "Информ" in text
    assert "Вопрос от студента" in text
    assert "Ответ администратора" in text


def test_mask_anonymous_data():
    assert mask_anonymous_data("Иван Иванов", True) == "Аноним"
    assert mask_anonymous_data("Иван Иванов", False) == "Иван Иванов"
    assert mask_anonymous_data(None, False) == "—"


async def test_get_user_tickets_only_own(db_session_maker):
    from core.ticket_service import get_user_tickets

    async with db_session_maker() as session:
        user = User(vk_id=111)
        other = User(vk_id=222)
        session.add_all([user, other])
        await session.flush()
        session.add_all(
            [
                Ticket(user_id=user.id, topic="Своя заявка"),
                Ticket(user_id=other.id, topic="Чужая заявка"),
            ]
        )
        await session.commit()

    tickets = await get_user_tickets(111, include_completed=True)
    assert len(tickets) == 1
    assert tickets[0].topic == "Своя заявка"


async def test_get_user_tickets_excludes_completed(db_session_maker):
    from core.ticket_service import get_user_tickets

    async with db_session_maker() as session:
        user = User(vk_id=333)
        session.add(user)
        await session.flush()
        session.add_all(
            [
                Ticket(user_id=user.id, topic="Активная"),
                Ticket(user_id=user.id, topic="Закрытая", status=TicketStatus.COMPLETED),
            ]
        )
        await session.commit()

    tickets = await get_user_tickets(333, include_completed=False)
    assert len(tickets) == 1
    assert tickets[0].topic == "Активная"


async def test_get_user_tickets_pagination(db_session_maker):
    """Пагинация: limit/offset работают и не нарушают порядок (новые сверху)."""
    from sqlalchemy import select

    from core.ticket_service import get_user_tickets

    async with db_session_maker() as session:
        user = User(vk_id=444)
        session.add(user)
        await session.commit()
        db_user = await session.scalar(select(User).where(User.vk_id == 444))

        for i in range(7):
            session.add(
                Ticket(
                    user_id=db_user.id,
                    topic=f"Заявка {i}",
                    created_at=datetime(2026, 9, 1, 10, i),
                )
            )
        await session.commit()

    first_page = await get_user_tickets(444, include_completed=True, limit=3, offset=0)
    second_page = await get_user_tickets(444, include_completed=True, limit=3, offset=3)

    assert len(first_page) == 3
    assert len(second_page) == 3
    # Второй набор не пересекается с первым по теме
    topics1 = {t.topic for t in first_page}
    topics2 = {t.topic for t in second_page}
    assert topics1.isdisjoint(topics2)
    # Новые сверху: на первой странице номера (по created_at) выше
    assert first_page[0].created_at >= first_page[1].created_at


@pytest.mark.asyncio
async def test_sync_unassigned_ticket_departments_preloaded_keywords(db_session_maker):
    """Проверка пакетного сопоставления отделов по предзагруженным ключевым словам (без N+1)."""
    from core.models import KnowledgeBase
    from core.ticket_service import sync_unassigned_ticket_departments

    async with db_session_maker() as session:
        dept_sport = Department(name="Спорткомплекс")
        dept_hostel = Department(name="Жилищно-бытовой отдел")
        dept_it = Department(name="IT Отдел")
        session.add_all([dept_sport, dept_hostel, dept_it])
        await session.flush()

        # База знаний для IT с ключевыми словами
        kb_it = KnowledgeBase(
            department_id=dept_it.id,
            keywords="роутер,вайфай,интернет",
            answer="Перезагрузите роутер",
        )
        session.add(kb_it)

        # 3 нераспределённые заявки:
        # 1. прямое совпадение по каноническим корням жил/быт
        t1 = Ticket(topic="Жилищные вопросы", description="Сломался шкаф")
        # 2. прямое совпадение по имени спорткомплекса
        t2 = Ticket(topic="Спорткомплекс", description="Расписание тренировок")
        # 3. совпадение по ключевым словам базы знаний (вайфай)
        t3 = Ticket(topic="Связь в комнате", description="Не работает вайфай")
        # 4. заявка без соответствий
        t4 = Ticket(topic="Непонятный вопрос", description="Что-то странное")
        session.add_all([t1, t2, t3, t4])
        await session.commit()

        t1_id, t2_id, t3_id, t4_id = t1.id, t2.id, t3.id, t4.id
        dept_sport_id, dept_hostel_id, dept_it_id = dept_sport.id, dept_hostel.id, dept_it.id

    updated = await sync_unassigned_ticket_departments()
    assert updated == 3

    async with db_session_maker() as session:
        t1_db = await session.get(Ticket, t1_id)
        t2_db = await session.get(Ticket, t2_id)
        t3_db = await session.get(Ticket, t3_id)
        t4_db = await session.get(Ticket, t4_id)

        assert t1_db.department_id == dept_hostel_id
        assert t2_db.department_id == dept_sport_id
        assert t3_db.department_id == dept_it_id
        assert t4_db.department_id is None


async def test_tickets_web_search_and_filters(db_session_maker):
    """Тест поиска и фильтрации заявок в веб-панели (без 422 и с поддержкой кириллицы)."""
    from core.models import WebRole, WebUser
    from web.main import app

    async with db_session_maker() as session:
        dept = Department(name="Учебная часть")
        session.add(dept)
        await session.flush()

        admin = WebUser(
            username="super_search_test",
            password_hash="hash",
            role=WebRole.SUPERADMIN,
        )
        session.add(admin)

        student = User(vk_id=777888, full_name="Иван Смирнов")
        session.add(student)
        await session.flush()

        t1 = Ticket(
            id=201,
            user_id=student.id,
            department_id=dept.id,
            topic="Вопрос о справке об обучении",
            description="Нужна справка для военкомата",
            status=TicketStatus.NEW,
        )
        t2 = Ticket(
            id=202,
            user_id=student.id,
            department_id=dept.id,
            topic="Стипендия за август",
            description="Не пришла выплата",
            status=TicketStatus.IN_PROGRESS,
        )
        session.add_all([t1, t2])
        await session.commit()

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Авторизуемся супер-админом в тестовой сессии
        response = await client.get(
            "/tickets/?q=&status=&department_id=",
            cookies={"session": "dummy"},
        )
        # Если не авторизован - редирект 302 на логин, а НЕ 422!
        assert response.status_code == 302

    # Теперь проверяем саму функцию tickets_page напрямую с авторизованным пользователем
    from starlette.requests import Request

    from web.routes.tickets import tickets_page

    user_ctx = {"username": "super_search_test", "role": "SUPERADMIN", "web_user_id": 1}

    # 1. Запрос с пустыми полями формы (как при нажатии Применить со всеми отделами)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/tickets/",
        "headers": [],
        "query_string": b"q=&status=&department_id=",
        "session": {},
    }
    req = Request(scope)
    req.state.session_id = "test-session"
    resp = await tickets_page(
        request=req,
        page=1,
        page_size=25,
        q="",
        status="",
        department_id="",
        user=user_ctx,
    )
    assert resp.status_code == 200

    # 2. Поиск по слову в нижнем регистре (кириллица: справка -> Справка)
    resp_search = await tickets_page(
        request=req,
        page=1,
        page_size=25,
        q="справка",
        status="",
        department_id="",
        user=user_ctx,
    )
    assert resp_search.status_code == 200
    assert len(resp_search.context["tickets"]) == 1
    assert resp_search.context["tickets"][0].id == 201

    # 3. Поиск по двум отдельным словам (справка + военкомат)
    resp_multi = await tickets_page(
        request=req,
        page=1,
        page_size=25,
        q="справка военкомат",
        status="",
        department_id="",
        user=user_ctx,
    )
    assert resp_multi.status_code == 200
    assert len(resp_multi.context["tickets"]) == 1

    # 4. Поиск по номеру заявки (#201 и №201)
    for q_id in ("201", "#201", "№201"):
        resp_id = await tickets_page(
            request=req,
            page=1,
            page_size=25,
            q=q_id,
            status="",
            department_id="",
            user=user_ctx,
        )
        assert len(resp_id.context["tickets"]) == 1
        assert resp_id.context["tickets"][0].id == 201

    # 5. Фильтрация по статусу (new, in_progress, В обработке)
    for st in ("new", "IN_PROGRESS", "В обработке"):
        resp_st = await tickets_page(
            request=req,
            page=1,
            page_size=25,
            q="",
            status=st,
            department_id="",
            user=user_ctx,
        )
        assert resp_st.status_code == 200
        assert len(resp_st.context["tickets"]) == 1


def test_match_department_in_memory_kb():
    """Проверка сопоставления отдела по ключевым словам базы знаний."""
    from core.models import KnowledgeBase

    dept1 = Department(id=1, name="Учебный отдел")
    dept2 = Department(id=2, name="Бухгалтерия")
    kb1 = KnowledgeBase(department_id=2, keywords="стипендия, выплата")
    matched = _match_department_in_memory("вопрос где стипендия", [dept1, dept2], [kb1])
    assert matched is not None
    assert matched.id == 2


@pytest.mark.asyncio
async def test_anonymous_ticket_has_normal_statuses(db_session_maker):
    """Анонимные заявки имеют стандартный жизненный цикл статусов (NEW -> IN_PROGRESS -> COMPLETED)."""
    from core.ticket_service import change_ticket_status, create_ticket

    ticket = await create_ticket(
        topic="Анонимный вопрос",
        description="Текст анонимной заявки",
        vk_id=12345,
        keep_identity=False,
    )
    assert ticket.is_anonymous is True
    assert ticket.status == TicketStatus.NEW
    assert ticket.user_id is None

    updated = await change_ticket_status(ticket.id, TicketStatus.IN_PROGRESS, "testadmin")
    assert updated is not None
    assert updated.status == TicketStatus.IN_PROGRESS

    completed = await change_ticket_status(ticket.id, TicketStatus.COMPLETED, "testadmin")
    assert completed is not None
    assert completed.status == TicketStatus.COMPLETED


@pytest.mark.asyncio
async def test_community_reply_auto_sets_in_progress(db_session_maker):
    from core.models import MessageAuthorType
    from core.services.community_reply_service import handle_community_message_reply
    from core.ticket_service import create_ticket

    student_vk = 77112233
    ticket = await create_ticket(
        topic="Вопрос студента",
        description="Нужна помощь с общежитием",
        vk_id=student_vk,
        keep_identity=True,
    )
    assert ticket.status == TicketStatus.NEW

    # Оператор отвечает со стороны сообщества ВКонтакте
    updated = await handle_community_message_reply(
        peer_id=student_vk,
        text="Здравствуйте! Мы взяли вашу заявку в работу.",
        admin_author_id=998877,
    )
    assert updated is not None
    assert updated.status == TicketStatus.IN_PROGRESS
    assert updated.response_text == "Здравствуйте! Мы взяли вашу заявку в работу."

    async with db_session_maker() as session:
        msgs = (
            await session.scalars(
                select(TicketMessage).where(TicketMessage.ticket_id == ticket.id)
            )
        ).all()
        admin_msgs = [m for m in msgs if m.author_type == MessageAuthorType.ADMIN]
        assert len(admin_msgs) == 1
        assert admin_msgs[0].message == "Здравствуйте! Мы взяли вашу заявку в работу."


@pytest.mark.asyncio
async def test_notifications_scoping_for_superadmin_and_department_admin(db_session_maker):
    from core.models import Admin, Department, User, UserRole, VkOutbox
    from core.ticket_service import create_ticket

    async with db_session_maker() as session:
        dept_a = Department(name="Отдел А")
        dept_b = Department(name="Отдел Б")
        session.add_all([dept_a, dept_b])
        await session.flush()

        u_super = User(vk_id=9001, full_name="Супер Админ")
        u_dept_a = User(vk_id=9002, full_name="Админ А")
        u_dept_b = User(vk_id=9003, full_name="Админ Б")
        session.add_all([u_super, u_dept_a, u_dept_b])
        await session.flush()

        sa = Admin(user_id=u_super.id, role=UserRole.SUPERADMIN, department_id=None)
        adm_a = Admin(user_id=u_dept_a.id, role=UserRole.ADMIN, department_id=dept_a.id)
        adm_b = Admin(user_id=u_dept_b.id, role=UserRole.ADMIN, department_id=dept_b.id)
        session.add_all([sa, adm_a, adm_b])
        await session.commit()

    # 1. Заявка в отдел А: уведомление получают админ отдела А (9002) и суперадмин (9001), но НЕ админ Б (9003)
    await create_ticket(
        topic="Заявка в Отдел А",
        description="Текст",
        vk_id=888001,
        keep_identity=True,
        department_name="Отдел А",
    )
    async with db_session_maker() as session:
        outbox_dept_a = (
            await session.scalars(select(VkOutbox).where(VkOutbox.text.like("%Заявка в Отдел А%")))
        ).all()
        recipient_vk_ids = {m.vk_id for m in outbox_dept_a}
        assert 9001 in recipient_vk_ids  # Суперадмин
        assert 9002 in recipient_vk_ids  # Админ Отдела А
        assert 9003 not in recipient_vk_ids  # Админ Отдела Б не должен получать!

    # 2. Общая заявка (без отдела): уведомление получает ТОЛЬКО суперадмин (9001)
    await create_ticket(
        topic="Общий вопрос без отдела",
        description="Текст",
        vk_id=888002,
        keep_identity=True,
        department_name=None,
    )
    async with db_session_maker() as session:
        outbox_general = (
            await session.scalars(
                select(VkOutbox).where(VkOutbox.text.like("%Общий вопрос без отдела%"))
            )
        ).all()
        recipient_vk_ids = {m.vk_id for m in outbox_general}
        assert 9001 in recipient_vk_ids  # Суперадмин
        assert 9002 not in recipient_vk_ids  # Обычные админы отделов не получают
        assert 9003 not in recipient_vk_ids


@pytest.mark.asyncio
async def test_non_anonymous_ticket_user_name_fetched(db_session_maker, monkeypatch):
    from unittest.mock import AsyncMock

    from core.models import User
    from core.ticket_service import create_ticket

    # Мокаем обращение к VK API users.get
    mock_fetch = AsyncMock(return_value="Петр Сидоров")
    monkeypatch.setattr("core.services.ticket_student_service.fetch_vk_user_name", mock_fetch)

    new_student_vk = 99881122
    ticket = await create_ticket(
        topic="Заявка с получением ФИО",
        description="Вопрос студента",
        vk_id=new_student_vk,
        keep_identity=True,
    )
    assert ticket.is_anonymous is False

    async with db_session_maker() as session:
        user = await session.scalar(select(User).where(User.vk_id == new_student_vk))
        assert user is not None
        assert user.full_name == "Петр Сидоров"
