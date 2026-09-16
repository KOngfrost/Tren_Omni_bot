import re
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from bots.vk.bot import events_handler, register_event_handler
from core.models import Department, Event


@pytest.mark.asyncio
async def test_dept_frame_access_and_idor(web_client):
    # Логинимся как суперадмин
    login_page = web_client.get("/auth/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)
    res_login = web_client.post(
        "/auth/login",
        data={"username": "testadmin", "password": "test_password_123", "csrf_token": token},
        follow_redirects=False,
    )
    assert res_login.status_code == 303

    # Создаем отдел
    dept_page = web_client.get("/departments/")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', dept_page.text).group(1)
    res_create = web_client.post(
        "/departments/create",
        data={"name": "IT Department", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert res_create.status_code == 200

    # Проверяем доступ к фрейму отдела
    res_frame = web_client.get("/dept/1/")
    assert res_frame.status_code == 200
    assert "IT Department" in res_frame.text


@pytest.mark.asyncio
async def test_event_registration_past_event_check(db_session_maker):
    now = datetime.now(UTC)
    async with db_session_maker() as session:
        dept = Department(name="Sport")
        session.add(dept)
        await session.flush()

        # Прошедшее мероприятие
        past_event = Event(
            department_id=dept.id, title="Past Match", event_date=now - timedelta(days=2)
        )
        # Будущее мероприятие
        future_event = Event(
            department_id=dept.id, title="Future Match", event_date=now + timedelta(days=2)
        )
        session.add_all([past_event, future_event])
        await session.commit()
        past_id = past_event.id
        future_id = future_event.id

    # Тестируем логику записи на прошедшее событие
    msg = MagicMock()
    msg.from_id = 555001
    msg.text = f"Записаться #{past_id}"
    msg.answer = AsyncMock()

    await register_event_handler(msg)
    msg.answer.assert_called_once()
    assert "Нельзя записаться на прошедшее мероприятие" in msg.answer.call_args[0][0]

    # Тестируем запись на будущее событие
    msg2 = MagicMock()
    msg2.from_id = 555001
    msg2.text = f"Записаться #{future_id}"
    msg2.answer = AsyncMock()

    await register_event_handler(msg2)
    msg2.answer.assert_called_once()
    assert "Вы зарегистрированы" in msg2.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_events_handler_empty_and_populated(db_session_maker):
    now = datetime.now(UTC)

    # 1. Проверяем, когда мероприятий нет
    msg = MagicMock()
    msg.from_id = 555002
    msg.text = "события"
    msg.answer = AsyncMock()

    await events_handler(msg)
    msg.answer.assert_called_once()
    assert "Ближайших мероприятий нет" in msg.answer.call_args[0][0]

    # 2. Создаём мероприятие и проверяем вывод
    async with db_session_maker() as session:
        dept = Department(name="Культура")
        session.add(dept)
        await session.flush()
        event = Event(
            department_id=dept.id,
            title="Студенческий вечер",
            description="Праздничный концерт в актовом зале",
            event_date=now + timedelta(days=3),
        )
        session.add(event)
        await session.commit()
        event_id = event.id

    msg2 = MagicMock()
    msg2.from_id = 555002
    msg2.text = "события"
    msg2.answer = AsyncMock()

    await events_handler(msg2)
    msg2.answer.assert_called_once()
    response_text = msg2.answer.call_args[0][0]
    assert "Ближайшие мероприятия:" in response_text
    assert "Студенческий вечер" in response_text
    assert f"#{event_id}" in response_text
    assert f"Записаться #{event_id}" in response_text
    assert "Праздничный концерт" in response_text


@pytest.mark.asyncio
async def test_delete_web_user_button_and_endpoint(web_client):
    import sqlite3

    # Логинимся как суперадмин
    login_page = web_client.get("/auth/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)
    res_login = web_client.post(
        "/auth/login",
        data={"username": "testadmin", "password": "test_password_123", "csrf_token": token},
        follow_redirects=False,
    )
    assert res_login.status_code == 303

    # Добавляем через базу дополнительного администратора с веб-пользователем
    conn = sqlite3.connect(f"file:{web_client._test_db_name}?mode=memory&cache=shared", uri=True)
    cur = conn.cursor()
    cur.execute("INSERT INTO departments (id, name) VALUES (501, 'Культура')")
    cur.execute("INSERT INTO users (id, vk_id, full_name) VALUES (502, 777111, 'Алексей Петров')")
    cur.execute(
        "INSERT INTO admins (id, user_id, department_id, role) VALUES (503, 502, 501, 'ADMIN')"
    )
    cur.execute(
        "INSERT INTO web_users (id, username, password_hash, role, department_id, admin_id, is_active) VALUES (504, 'alex_web', 'fake_hash', 'DEPARTMENT_ADMIN', 501, 503, 1)"
    )
    conn.commit()
    conn.close()

    # Проверяем отображение кнопки на странице admins.html
    page = web_client.get("/admin/admins/")
    assert page.status_code == 200
    assert "alex_web" in page.text
    assert "Отозвать веб-доступ" in page.text
    assert "/admin/admins/web-users/504/delete" in page.text

    # Отправляем запрос на удаление WebUser
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    del_resp = web_client.post(
        "/admin/admins/web-users/504/delete",
        data={"csrf_token": csrf},
        follow_redirects=True,
    )
    assert del_resp.status_code == 200
    assert "Веб-пользователь удалён" in del_resp.text

    # Проверяем, что в БД запись web_users удалена, а admin остался
    conn = sqlite3.connect(f"file:{web_client._test_db_name}?mode=memory&cache=shared", uri=True)
    cur = conn.cursor()
    cur.execute("SELECT id FROM web_users WHERE id = 504")
    assert cur.fetchone() is None
    cur.execute("SELECT id FROM admins WHERE id = 503")
    assert cur.fetchone() is not None
    conn.close()
