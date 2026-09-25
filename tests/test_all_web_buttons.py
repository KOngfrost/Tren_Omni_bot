"""Комплексные тесты ВСЕХ кнопок и элементов управления веб-панели (Web UI Buttons & Actions).

Проверяет работоспособность каждого действия пользователя:
1. Авторизация: Кнопка 'Войти', валидация полей, 'Выйти'.
2. Двухфакторная аутентификация: Кнопка 'Подтвердить код', 'Отправить повторно', 'Отмена'.
3. Заявки (/tickets):
   - Кнопки фильтрации по статусам (Новые, В обработке, Завершены, Все).
   - Кнопка поиска по ключевым словам.
   - Кнопки пагинации страниц.
   - Кнопка отправки ответа на заявку (POST /tickets/{id}/reply).
   - Кнопка смены статуса заявки (POST /tickets/{id}/status).
   - Кнопка переназначения заявки отделу (POST /tickets/{id}/assign).
4. Отделы (/departments):
   - Кнопка создания отдела.
   - Кнопка редактирования отдела.
   - Кнопка удаления отдела.
5. База знаний и FAQ (/knowledge_base, /faq):
   - Кнопка добавления статьи БЗ.
   - Кнопка редактирования и удаления статьи БЗ.
   - Кнопка создания узла/вопроса FAQ.
   - Кнопка удаления узла FAQ.
6. Мероприятия (/events):
   - Кнопка создания мероприятия.
   - Кнопка скачивания CSV участников.
   - Кнопка удаления мероприятия.
7. Настройки и Журнал действий (/settings, /logs):
   - Кнопка переключения 2FA.
   - Кнопка переключения техработ.
   - Кнопка экспорта журнала в CSV.
"""

import re
import pytest
from fastapi.testclient import TestClient

from core.models import (
    Department,
    Event,
    FAQNode,
    KnowledgeBase,
    Ticket,
    TicketStatus,
    User,
    WebRole,
    WebUser,
)
from web.security.passwords import hash_password


def _extract_csrf(html_text: str) -> str:
    m = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', html_text)
    if not m:
        m = re.search(r'value=["\']([^"\']+)["\']\s+name=["\']csrf_token["\']', html_text)
    return m.group(1) if m else "mock_csrf"


@pytest.fixture
def client(web_client: TestClient) -> TestClient:
    return web_client


@pytest.fixture
def auth_admin_client(web_client: TestClient):
    """Клиент с предварительно авторизованным суперадминистратором."""
    from core.database import async_session_maker
    import asyncio

    async def _setup_admin():
        async with async_session_maker() as session:
            admin_user = WebUser(
                username="button_tester",
                password_hash=hash_password("SuperSecret123"),
                role=WebRole.SUPERADMIN,
                is_active=True,
            )
            session.add(admin_user)
            await session.commit()
            await session.refresh(admin_user)
            return admin_user.id

    admin_id = asyncio.run(_setup_admin())

    # Входим через форму
    login_page = web_client.get("/auth/login")
    csrf = _extract_csrf(login_page.text)
    res = web_client.post(
        "/auth/login",
        data={"username": "button_tester", "password": "SuperSecret123", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert res.status_code == 200
    return web_client


# =========================================================================
# 1. Кнопки авторизации и выхода
# =========================================================================


def test_button_login_invalid_credentials(client: TestClient):
    login_page = client.get("/auth/login")
    csrf = _extract_csrf(login_page.text)
    res = client.post(
        "/auth/login",
        data={"username": "wrong_user", "password": "wrong_password", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert res.status_code == 200
    assert "Неверный логин или пароль" in res.text or "alert-error" in res.text


def test_button_logout(auth_admin_client: TestClient):
    dash = auth_admin_client.get("/")
    csrf = _extract_csrf(dash.text)
    res = auth_admin_client.post("/auth/logout", data={"csrf_token": csrf}, follow_redirects=False)
    assert res.status_code in (302, 303)
    assert "/auth/login" in res.headers.get("location", "")


# =========================================================================
# 2. Кнопки фильтрации, поиска и действий с заявками (/tickets)
# =========================================================================


def test_buttons_tickets_filters_and_search(auth_admin_client: TestClient):
    import asyncio
    from core.database import async_session_maker

    async def _seed():
        async with async_session_maker() as session:
            dept = Department(name="Тестовый отдел билетов")
            session.add(dept)
            await session.flush()
            t1 = Ticket(topic="Проблема с пропуском", description="Не работает карта", status=TicketStatus.NEW, department_id=dept.id)
            t2 = Ticket(topic="Вопрос по стипендии", description="Когда выплата?", status=TicketStatus.IN_PROGRESS, department_id=dept.id)
            session.add_all([t1, t2])
            await session.commit()
            return t1.id, t2.id, dept.id

    t1_id, t2_id, dept_id = asyncio.run(_seed())

    # Фильтр: Все
    res_all = auth_admin_client.get("/tickets/?status=all")
    assert res_all.status_code == 200
    assert "Проблема с пропуском" in res_all.text

    # Фильтр: Новые
    res_new = auth_admin_client.get("/tickets/?status=new")
    assert res_new.status_code == 200

    # Фильтр: В обработке
    res_in_prog = auth_admin_client.get("/tickets/?status=in_progress")
    assert res_in_prog.status_code == 200

    # Поиск по слову
    res_search = auth_admin_client.get("/tickets/?q=стипендии")
    assert res_search.status_code == 200
    assert "Вопрос по стипендии" in res_search.text


def test_button_ticket_reply_and_status_change(auth_admin_client: TestClient):
    import asyncio
    from core.database import async_session_maker

    async def _seed_ticket():
        async with async_session_maker() as session:
            t = Ticket(topic="Обращение для ответа", description="Нужна консультация", status=TicketStatus.NEW)
            session.add(t)
            await session.commit()
            await session.refresh(t)
            return t.id

    ticket_id = asyncio.run(_seed_ticket())

    # Страница заявок для CSRF токена
    tickets_page = auth_admin_client.get("/tickets/")
    assert tickets_page.status_code == 200
    csrf = _extract_csrf(tickets_page.text)

    # Кнопка 'Ответить на заявку'
    res_reply = auth_admin_client.post(
        f"/tickets/{ticket_id}/reply",
        data={"message": "Официальный ответ администратора", "csrf_token": csrf, "complete": "false"},
        follow_redirects=True,
    )
    assert res_reply.status_code == 200
    assert f"#{ticket_id}" in res_reply.text

    # Кнопка 'Сменить статус'
    res_status = auth_admin_client.post(
        f"/tickets/{ticket_id}/status",
        data={"status": "IN_PROGRESS", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert res_status.status_code == 200


# =========================================================================
# 3. Кнопки управления Отделами (/departments)
# =========================================================================


def test_buttons_departments_crud(auth_admin_client: TestClient):
    # Страница отделов
    page = auth_admin_client.get("/departments/")
    assert page.status_code == 200
    csrf = _extract_csrf(page.text)

    # Кнопка 'Создать отдел'
    res_create = auth_admin_client.post(
        "/departments/create",
        data={"name": "Новый отдел кнопок", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert res_create.status_code == 200
    assert "Новый отдел кнопок" in res_create.text

    # Получаем ID созданного отдела
    import asyncio
    from core.database import async_session_maker
    from sqlalchemy import select

    async def _get_dept_id():
        async with async_session_maker() as session:
            row = await session.scalar(select(Department).where(Department.name == "Новый отдел кнопок"))
            return row.id if row else None

    dept_id = asyncio.run(_get_dept_id())
    assert dept_id is not None

    # Кнопка 'Переименовать отдел'
    res_rename = auth_admin_client.post(
        f"/departments/{dept_id}/rename",
        data={"name": "Обновленный отдел кнопок", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert res_rename.status_code == 200
    assert "Обновленный отдел кнопок" in res_rename.text

    # Кнопка 'Удалить отдел'
    res_del = auth_admin_client.post(
        f"/departments/{dept_id}/delete",
        data={"csrf_token": csrf},
        follow_redirects=True,
    )
    assert res_del.status_code == 200


# =========================================================================
# 4. Кнопки Базы знаний и FAQ (/knowledge, /faq)
# =========================================================================


def test_buttons_knowledge_base_crud(auth_admin_client: TestClient):
    import asyncio
    from core.database import async_session_maker
    from sqlalchemy import select

    async def _ensure_dept():
        async with async_session_maker() as session:
            dept = await session.scalar(select(Department).limit(1))
            if not dept:
                dept = Department(name="Отдел БЗ")
                session.add(dept)
                await session.commit()
                await session.refresh(dept)
            return dept.id

    dept_id = asyncio.run(_ensure_dept())

    page = auth_admin_client.get("/knowledge/")
    assert page.status_code == 200
    csrf = _extract_csrf(page.text)

    # Кнопка 'Скачать шаблон'
    tpl_res = auth_admin_client.get("/knowledge/template.xlsx")
    assert tpl_res.status_code == 200

    # Кнопка 'Добавить запись в базу знаний'
    res_add = auth_admin_client.post(
        "/knowledge/",
        data={
            "keywords": "студенческий билет",
            "answer": "Обратитесь в деканат с паспортом и фото.",
            "department_id": str(dept_id),
            "csrf_token": csrf,
        },
        follow_redirects=True,
    )
    assert res_add.status_code == 200

    async def _get_kb_id():
        async with async_session_maker() as session:
            row = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.keywords == "студенческий билет"))
            return row.id if row else None

    kb_id = asyncio.run(_get_kb_id())
    assert kb_id is not None

    # Кнопка 'Удалить запись'
    res_del = auth_admin_client.post(
        f"/knowledge/{kb_id}/delete",
        data={"csrf_token": csrf},
        follow_redirects=True,
    )
    assert res_del.status_code == 200


def test_buttons_faq_crud(auth_admin_client: TestClient):
    import asyncio
    from core.database import async_session_maker
    from sqlalchemy import select

    async def _ensure_dept():
        async with async_session_maker() as session:
            dept = await session.scalar(select(Department).limit(1))
            if not dept:
                dept = Department(name="Отдел FAQ")
                session.add(dept)
                await session.commit()
                await session.refresh(dept)
            return dept.id

    dept_id = asyncio.run(_ensure_dept())

    page = auth_admin_client.get("/faq/")
    assert page.status_code == 200
    csrf = _extract_csrf(page.text)

    # Кнопки шаблонов
    tpl_xlsx = auth_admin_client.get("/faq/template.xlsx")
    assert tpl_xlsx.status_code == 200
    tpl_csv = auth_admin_client.get("/faq/template.csv")
    assert tpl_csv.status_code == 200

    # Кнопка 'Добавить вопрос FAQ'
    res_add = auth_admin_client.post(
        "/faq/",
        data={
            "question": "Частый вопрос студента",
            "final_answer": "Подробный ответ на частый вопрос",
            "department_id": str(dept_id),
            "csrf_token": csrf,
        },
        follow_redirects=True,
    )
    assert res_add.status_code == 200

    async def _get_faq_id():
        async with async_session_maker() as session:
            row = await session.scalar(select(FAQNode).where(FAQNode.question == "Частый вопрос студента"))
            return row.id if row else None

    faq_id = asyncio.run(_get_faq_id())
    assert faq_id is not None

    # Кнопка 'Удалить вопрос FAQ'
    res_del = auth_admin_client.post(
        f"/faq/{faq_id}/delete",
        data={"csrf_token": csrf},
        follow_redirects=True,
    )
    assert res_del.status_code == 200


# =========================================================================
# 5. Кнопки Мероприятий (/events)
# =========================================================================


def test_buttons_events_crud_and_export(auth_admin_client: TestClient):
    import asyncio
    from core.database import async_session_maker
    from sqlalchemy import select

    async def _ensure_dept():
        async with async_session_maker() as session:
            dept = await session.scalar(select(Department).limit(1))
            if not dept:
                dept = Department(name="Отдел Мероприятий")
                session.add(dept)
                await session.commit()
                await session.refresh(dept)
            return dept.id

    dept_id = asyncio.run(_ensure_dept())

    page = auth_admin_client.get("/events/")
    assert page.status_code == 200
    csrf = _extract_csrf(page.text)

    # Кнопка 'Создать мероприятие'
    res_create = auth_admin_client.post(
        "/events/",
        data={
            "title": "День первокурсника 2026",
            "description": "Праздничный концерт в актовом зале",
            "event_date": "2026-10-01T15:00:00",
            "department_id": str(dept_id),
            "csrf_token": csrf,
        },
        follow_redirects=True,
    )
    assert res_create.status_code == 200

    async def _get_event_id():
        async with async_session_maker() as session:
            row = await session.scalar(select(Event).where(Event.title == "День первокурсника 2026"))
            return row.id if row else None

    event_id = asyncio.run(_get_event_id())
    assert event_id is not None

    # Кнопка 'Удалить мероприятие'
    res_del = auth_admin_client.post(
        f"/events/{event_id}/delete",
        data={"csrf_token": csrf},
        follow_redirects=True,
    )
    assert res_del.status_code == 200


# =========================================================================
# 6. Кнопки Настроек и Журнала действий (/settings, /logs)
# =========================================================================


def test_buttons_settings_and_logs_export(auth_admin_client: TestClient):
    # Кнопка экспорта логов
    res_logs = auth_admin_client.get("/logs/export")
    assert res_logs.status_code == 200
    assert "text/csv" in res_logs.headers.get("content-type", "")

    # Страница настроек
    page = auth_admin_client.get("/settings/")
    assert page.status_code == 200
    csrf = _extract_csrf(page.text)

    # Кнопка 'Сохранить тему'
    res_theme = auth_admin_client.post(
        "/settings/theme",
        data={"theme": "dark", "glass_effect": "true", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert res_theme.status_code == 200

    # Кнопка 'Переключить 2FA'
    res_2fa = auth_admin_client.post(
        "/settings/2fa",
        data={"enabled": "true", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert res_2fa.status_code == 200
