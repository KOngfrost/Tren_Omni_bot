"""
Тесты новых возможностей: метрики сайта и бота, индикатор активности
администраторов, права временного админа, маршрутизация уведомлений
и фильтр заявок без отдела.

Соответствуют плану тестирования ТЗ:
1. Права временного администратора: запрет создания/удаления учётных записей
   при сохранённом доступе к заявкам.
2. Выборка уведомлений: админ отдела видит свои заявки + общие, но не чужие.
3. Рассылка outbox: заявка без отдела генерирует сообщения для всех админов,
   заявка отдела — только для своего отдела и суперадминов.
4. Фильтр department_id=none и бейдж «Общее обращение».
5. Единое время по МСК (core/time_utils) и индикатор активности.
"""

import re

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from core.admin_presence import ADMIN_PRESENCE_TTL_SECONDS, clear_presence
from core.models import (
    Admin,
    Department,
    Ticket,
    TicketStatus,
    User,
    UserRole,
    WebRole,
    WebUser,
)
from core.ticket_service import create_ticket
from core.time_utils import (
    day_start_app_tz,
    format_app_datetime,
    humanize_last_seen,
    now_app_tz,
)
from web.dependencies import is_temporary, is_temporary_user
from web.main import app
from web.security.passwords import hash_password

# asyncio_mode = "auto" в pyproject.toml: async-тесты запускаются без декларатора.


# ============================== Фикстуры ==============================


async def _seed_departments(session) -> tuple[Department, Department]:
    """Два отдела: для проверки изоляции видимости между администраторами."""
    first = Department(name="Отдел А")
    second = Department(name="Отдел Б")
    session.add_all([first, second])
    await session.flush()
    return first, second


async def _make_web_user(
    session,
    *,
    username: str,
    role: WebRole = WebRole.DEPARTMENT_ADMIN,
    department_id: int | None = None,
    admin_id: int | None = None,
    expires_at=None,
) -> WebUser:
    """Создать веб-администратора с нужными параметрами временности."""
    web_user = WebUser(
        username=username,
        password_hash=hash_password("temp_password_123"),
        role=role,
        department_id=department_id,
        admin_id=admin_id,
        expires_at=expires_at,
        is_active=True,
    )
    session.add(web_user)
    await session.flush()
    return web_user


async def _make_admin(session, *, vk_id: int, department_id: int | None, role=UserRole.ADMIN):
    """Создать VK-администратора (Admin) с привязанным User."""
    vk_user = User(vk_id=vk_id, full_name=f"Админ {vk_id}")
    session.add(vk_user)
    await session.flush()
    admin = Admin(user_id=vk_user.id, department_id=department_id, role=role)
    session.add(admin)
    await session.flush()
    return admin


async def _login(client: AsyncClient, username: str, password: str = "temp_password_123"):
    """Войти в панель под указанным логином (2FA отключена в фикстурах)."""
    page = await client.get("/auth/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    return await client.post(
        "/auth/login",
        data={"username": username, "password": password, "csrf_token": csrf},
        follow_redirects=False,
    )


def _csrf_of(html: str) -> str:
    """Извлечь CSRF-токен из HTML или вернуть пустую строку."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return match.group(1) if match else ""


# =================== 1. Права временного администратора ===================


async def test_temporary_admin_flag_derived_from_db(db_session_maker):
    """is_temporary вычисляется из БД: срок или отсутствие привязки к VK."""
    async with db_session_maker() as session:
        dept_a, _ = await _seed_departments(session)

        # Постоянный: есть admin_id, нет срока.
        permanent_admin = await _make_admin(session, vk_id=101, department_id=dept_a.id)
        permanent = await _make_web_user(
            session, username="perm_admin", admin_id=permanent_admin.id, department_id=dept_a.id
        )
        # Временный: срок ограничен.
        temporary = await _make_web_user(
            session,
            username="temp_admin",
            expires_at=now_app_tz().replace(microsecond=0),
        )
        # Временный: без привязки к VK-админу.
        unlinked = await _make_web_user(session, username="qa_unlinked")
        await session.commit()

        assert is_temporary_user(permanent) is False
        assert is_temporary_user(temporary) is True
        assert is_temporary_user(unlinked) is True
        assert is_temporary_user(None) is False


async def test_temporary_superadmin_cannot_create_accounts(db_session_maker):
    """Временный админ с ролью SUPERADMIN всё равно не может создать админа.

    Ключевая проверка ТЗ: роль в сессии не является источником прав —
    ограничение задаётся признаком временного доступа из БД.
    """
    async with db_session_maker() as session:
        await _make_web_user(
            session,
            username="temp_super",
            role=WebRole.SUPERADMIN,  # даже супердмин!
            expires_at=now_app_tz().replace(microsecond=0),
        )
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, "temp_super")

        before = await client.get("/admin/admins/")
        assert before.status_code == 200
        # В интерфейсе кнопка заблокирована, а форма отсутствует.
        assert 'data-open-modal="modal"' not in before.text
        assert "Временный администратор" in before.text

        # Прямой POST в обход интерфейса тоже должен быть отклонён.
        res = await client.post(
            "/admin/admins/",
            data={
                "vk_id": "555000111",
                "full_name": "Взломщик",
                "username": "hacked_admin",
                "password": "SuperSecret123",
                "role": "superadmin",
                "csrf_token": _csrf_of(before.text),
            },
            follow_redirects=False,
        )
        assert res.status_code == 302
        assert res.headers["location"] == "/admin/admins/"

    async with db_session_maker() as session:
        created = await session.scalar(select(WebUser).where(WebUser.username == "hacked_admin"))
        assert created is None, "Временный админ не должен создавать учётные записи"


async def test_temporary_admin_cannot_delete_web_user(db_session_maker):
    """Временный админ не может удалить чужую учётную запись."""
    async with db_session_maker() as session:
        await _make_web_user(
            session,
            username="temp_deleter",
            role=WebRole.SUPERADMIN,
            expires_at=now_app_tz().replace(microsecond=0),
        )
        victim = await _make_web_user(session, username="victim_admin")
        await session.commit()
        victim_id = victim.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, "temp_deleter")
        page = await client.get("/admin/admins/")
        res = await client.post(
            f"/admin/admins/web-users/{victim_id}/delete",
            data={"csrf_token": _csrf_of(page.text)},
            follow_redirects=False,
        )
        assert res.status_code == 302

    async with db_session_maker() as session:
        assert await session.get(WebUser, victim_id) is not None


async def test_temporary_admin_keeps_ticket_access(db_session_maker):
    """Ограничение НЕ затрагивает работу с заявками: доступ к списку и дашборду есть."""
    async with db_session_maker() as session:
        dept_a, _ = await _seed_departments(session)
        student = User(vk_id=424242, full_name="Студент")
        session.add(student)
        await session.flush()
        session.add(
            Ticket(
                user_id=student.id,
                department_id=dept_a.id,
                topic="Вопрос",
                status=TicketStatus.NEW,
            )
        )
        await _make_web_user(
            session,
            username="temp_worker",
            department_id=dept_a.id,
            expires_at=now_app_tz().replace(microsecond=0),
        )
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, "temp_worker")

        dashboard = await client.get("/")
        assert dashboard.status_code == 200

        tickets = await client.get("/tickets/")
        assert tickets.status_code == 200
        assert "Вопрос" in tickets.text  # заявка своего отдела доступна

        # Смена статуса заявки остаётся разрешённой.
        change = await client.post(
            "/tickets/1/status",
            data={"status": "В обработке", "csrf_token": _csrf_of(tickets.text)},
            follow_redirects=False,
        )
        assert change.status_code == 303


def test_is_temporary_helper_handles_bootstrap_session():
    """Bootstrap-сессия из .env считается постоянной (нет срока и web_user_id)."""
    assert is_temporary({"username": "boot", "role": "superadmin", "bootstrap": True}) is False
    # Сессия, созданная до обновления кода: флага нет, но срок указан.
    assert is_temporary({"username": "old", "expires_at": now_app_tz()}) is True
    assert is_temporary({"username": "old", "is_temporary": True}) is True


# ============ 2. Маршрутизация уведомлений (отдел vs общее) ============


async def test_general_ticket_notifies_superadmins_only(db_session_maker):
    """Заявка без отдела: уведомления поступают только суперадминистраторам."""
    from core.models import VkOutbox

    async with db_session_maker() as session:
        dept_a, dept_b = await _seed_departments(session)
        await _make_admin(session, vk_id=1001, department_id=dept_a.id)
        await _make_admin(session, vk_id=1002, department_id=dept_b.id)
        await _make_admin(session, vk_id=1003, department_id=None, role=UserRole.SUPERADMIN)
        student = User(vk_id=900001, full_name="Студент Общий")
        session.add(student)
        await session.commit()

    ticket = await create_ticket(
        topic="Общий вопрос",
        description="К кому обратиться?",
        vk_id=900001,
        keep_identity=True,
        department_name=None,  # БЕЗ отдела
    )
    assert ticket is not None
    assert ticket.department_id is None

    async with db_session_maker() as session:
        rows = list((await session.scalars(select(VkOutbox))).all())
        targets = {row.vk_id for row in rows}
        assert targets == {1003}
        # Текст явно помечен как общее обращение.
        assert any("Новое общее обращение" in row.text for row in rows)
        assert any("[Общий вопрос]" in row.text for row in rows)


async def test_department_ticket_notifies_only_own_department_and_superadmins(db_session_maker):
    """Заявка отдела: администраторы ДРУГИХ отделов не спамятся."""
    from core.models import VkOutbox

    async with db_session_maker() as session:
        dept_a, dept_b = await _seed_departments(session)
        await _make_admin(session, vk_id=2001, department_id=dept_a.id)
        await _make_admin(session, vk_id=2002, department_id=dept_b.id)
        await _make_admin(session, vk_id=2003, department_id=None, role=UserRole.SUPERADMIN)
        student = User(vk_id=900002, full_name="Студент Отдел")
        session.add(student)
        await session.commit()

    ticket = await create_ticket(
        topic="Вопрос по отделу",
        description="Только для отдела А",
        vk_id=900002,
        keep_identity=True,
        department_name="Отдел А",
    )
    assert ticket is not None
    assert ticket.department_id is not None

    async with db_session_maker() as session:
        rows = list((await session.scalars(select(VkOutbox))).all())
        targets = {row.vk_id for row in rows}
        # Админ отдела А + суперадмин. Админ отдела Б исключён.
        assert targets == {2001, 2003}
        assert 2002 not in targets
        # Нет повторов: суперадмин не должен получить два одинаковых сообщения.
        assert len([r for r in rows if r.vk_id == 2003]) == 1


async def test_notification_counters_show_own_and_general_tickets(db_session_maker):
    """Админ отдела в счётчиках видит свои + общие заявки, но не чужие."""
    async with db_session_maker() as session:
        dept_a, dept_b = await _seed_departments(session)
        student = User(vk_id=900003, full_name="Студент")
        session.add(student)
        await session.flush()
        # Своя заявка, чужая заявка и общее обращение.
        session.add_all(
            [
                Ticket(
                    user_id=student.id,
                    department_id=dept_a.id,
                    topic="Своя",
                    status=TicketStatus.NEW,
                ),
                Ticket(
                    user_id=student.id,
                    department_id=dept_b.id,
                    topic="Чужая",
                    status=TicketStatus.NEW,
                ),
                Ticket(
                    user_id=student.id, department_id=None, topic="Общая", status=TicketStatus.NEW
                ),
            ]
        )
        await _make_web_user(session, username="counter_admin", department_id=dept_a.id)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, "counter_admin")
        res = await client.get("/api/counters")
        assert res.status_code == 200
        body = res.json()["data"]

        # Своя + общая = 2; чужая скрыта.
        assert body["new_tickets_count"] == 2

        items = body["items"]
        # Ровно одно общее обращение, и оно помечено бейджем.
        general_items = [i for i in items if i["is_general"]]
        assert len(general_items) == 1
        assert general_items[0]["general_label"] == "Общее обращение"
        assert general_items[0]["department"] == "Без отдела"

        # Заявок чужого отдела в выдаче нет.
        depts = {i["department"] for i in items}
        assert "Отдел Б" not in depts
        # Своя заявка присутствует.
        assert "Отдел А" in depts


# ============ 3. Фильтр заявок без отдела и виджеты дашборда ============


async def test_tickets_filter_department_none(db_session_maker):
    """?department_id=none показывает только обращения без отдела."""
    async with db_session_maker() as session:
        dept_a, _ = await _seed_departments(session)
        student = User(vk_id=900004, full_name="Студент")
        session.add(student)
        await session.flush()
        session.add_all(
            [
                Ticket(
                    user_id=student.id,
                    department_id=dept_a.id,
                    topic="Отделная",
                    status=TicketStatus.NEW,
                ),
                Ticket(
                    user_id=student.id,
                    department_id=None,
                    topic="Общее обращение тест",
                    status=TicketStatus.NEW,
                ),
            ]
        )
        await _make_web_user(session, username="filter_super", role=WebRole.SUPERADMIN)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, "filter_super")

        res = await client.get("/tickets/?department_id=none")
        assert res.status_code == 200
        # В списке только общие обращения.
        assert "Общее обращение тест" in res.text
        assert "Отделная" not in res.text
        # Пункт фильтра присутствует.
        assert "Без отдела (общие)" in res.text
        # Бейдж вместо прочерка в колонке «Отдел».
        assert "badge-general" in res.text


async def test_dashboard_shows_unassigned_widget(db_session_maker):
    """Дашборд показывает отдельный блок «Общие (без отдела)» со ссылкой на фильтр."""
    async with db_session_maker() as session:
        dept_a, _ = await _seed_departments(session)
        student = User(vk_id=900005, full_name="Студент")
        session.add(student)
        await session.flush()
        session.add_all(
            [
                Ticket(
                    user_id=student.id,
                    department_id=dept_a.id,
                    topic="Отделная",
                    status=TicketStatus.NEW,
                ),
                Ticket(
                    user_id=student.id, department_id=None, topic="Общая1", status=TicketStatus.NEW
                ),
                Ticket(
                    user_id=student.id, department_id=None, topic="Общая2", status=TicketStatus.NEW
                ),
            ]
        )
        await _make_web_user(session, username="dash_super", role=WebRole.SUPERADMIN)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, "dash_super")
        res = await client.get("/")
        assert res.status_code == 200
        assert "Общие (без отдела)" in res.text
        # Прямая ссылка на фильтр по нераспределённым заявкам.
        assert "/tickets/?department_id=none" in res.text
        assert "Всего общих обращений" in res.text
        # В переработанном дашборде строка подписана просто «Требуют внимания».
        assert "Требуют внимания" in res.text


# ============ 4. Единое время по МСК и индикатор активности ============


async def test_time_helpers_use_app_timezone():
    """now_app_tz/format_app_datetime работают в APP_TIMEZONE (МСК)."""
    now = now_app_tz()
    assert now.tzinfo is not None
    assert now.utcoffset().total_seconds() == 3 * 3600  # Europe/Moscow = UTC+3
    # Дата без таймзона трактуется как UTC и переводится в МСК.
    from datetime import UTC, datetime

    naive = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    assert format_app_datetime(naive) == "15.01.2026 15:00"
    assert format_app_datetime(None) == "—"
    assert format_app_datetime(naive, "%d.%m") == "15.01"


async def test_humanize_last_seen_variants():
    """Время последнего визита читается по-человечески и в МСК."""
    from datetime import timedelta

    now = now_app_tz()
    assert humanize_last_seen(None) == "Не входил"
    assert humanize_last_seen(now - timedelta(minutes=2)) == "Только что"
    assert humanize_last_seen(now - timedelta(minutes=20)).endswith("мин назад")
    # «Три часа назад» — это сегодня или вчера в зависимости от времени суток
    # запуска; жёстко требовать «сегодня» значило бы ронять тест после полуночи.
    three_hours_ago = now - timedelta(hours=3)
    expected_prefix = "сегодня в " if three_hours_ago.date() == now.date() else "вчера в "
    assert humanize_last_seen(three_hours_ago).startswith(expected_prefix)


async def test_presence_ttl_and_online_detection(db_session_maker):
    """TTL присутствия — 120 секунд (конфиг), а heartbeat даёт «Онлайн»."""
    from core.admin_presence import get_online_ids, is_online, touch_presence
    from core.config import settings

    # TTL сокращён с 15 минут до 120 секунд: индикатор отражает текущую
    # активность, а не «заходил в течение четверти часа». Значение берётся
    # из конфигурации и ограничено снизу, чтобы не зависеть от .env.
    assert ADMIN_PRESENCE_TTL_SECONDS == settings.ADMIN_PRESENCE_TTL_SECONDS
    assert ADMIN_PRESENCE_TTL_SECONDS >= 30

    await clear_presence(999001)
    assert await is_online(999001) is False

    await touch_presence(999001)
    assert await is_online(999001) is True
    assert 999001 in await get_online_ids()

    await clear_presence(999001)
    assert await is_online(999001) is False


async def test_admins_page_shows_presence_badges(db_session_maker):
    """Страница администраторов показывает индикатор и текст статуса."""
    async with db_session_maker() as session:
        # Суперадмин с привязкой к VK-админу, чтобы сам быть постоянным.
        viewer_admin = await _make_admin(
            session, vk_id=900009, department_id=None, role=UserRole.SUPERADMIN
        )
        await _make_web_user(
            session,
            username="presence_viewer",
            role=WebRole.SUPERADMIN,
            admin_id=viewer_admin.id,
        )
        await _make_web_user(session, username="presence_target")
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client, "presence_viewer")
        res = await client.get("/admin/admins/")
        assert res.status_code == 200
        # Есть индикатор и текстовый бейдж статуса.
        assert "status-dot" in res.text
        assert "status-dot-offline" in res.text or "status-dot-online" in res.text
        assert "Офлайн" in res.text or "Онлайн" in res.text
        assert "Был в сети" in res.text
        assert "администраторов онлайн" in res.text


# ============ 5. Бизнес-метрики сайта и бота в Telegram ============


async def test_collect_app_metrics_counts_business_data(db_session_maker):
    """app_metrics собирает заявки, пользователей, outbox и сообщения диалога."""
    from datetime import timedelta

    from bots.telegram.app_metrics import collect_app_metrics
    from core.models import MessageAuthorType, PartnershipRequest, TicketMessage, VkOutbox

    now = now_app_tz()
    async with db_session_maker() as session:
        dept_a, _ = await _seed_departments(session)
        student = User(vk_id=900010, full_name="Студент", created_at=day_start_app_tz())
        session.add(student)
        await session.flush()
        new_ticket = Ticket(user_id=student.id, topic="Без отдела", status=TicketStatus.NEW)
        done_ticket = Ticket(
            user_id=student.id,
            department_id=dept_a.id,
            topic="Готово",
            status=TicketStatus.COMPLETED,
            # Полночь текущих суток: заведомо «сегодня» в любой момент запуска
            # (в отличие от «час назад» — это уже вчера вскоре после полуночи).
            updated_at=day_start_app_tz(),
        )
        session.add_all([new_ticket, done_ticket])
        await session.flush()
        session.add_all(
            [
                TicketMessage(
                    ticket_id=new_ticket.id,
                    author_type=MessageAuthorType.USER,
                    message="Привет",
                    created_at=day_start_app_tz(),
                ),
                VkOutbox(
                    vk_id=1,
                    text="pending",
                    status="pending",
                    created_at=now - timedelta(hours=1),
                ),
                VkOutbox(
                    vk_id=2,
                    text="failed",
                    status="failed",
                    created_at=now - timedelta(hours=1),
                ),
                PartnershipRequest(
                    vk_id=900011,
                    proposal_text="Сотрудничество",
                    status="new",
                    created_at=now - timedelta(hours=1),
                ),
            ]
        )
        await session.commit()

    metrics = await collect_app_metrics()
    site, bot = metrics["site"], metrics["bot"]

    assert site["tickets_unassigned"] == 1
    assert site["tickets_unassigned_new"] == 1
    assert site["tickets_completed_today"] == 1
    assert site["partnerships_new"] == 1
    assert bot["outbox_pending"] == 1
    assert bot["outbox_failed"] == 1
    assert bot["dialog_messages_today"] == 1
    assert bot["users_total"] == 1
    assert bot["users_today"] == 1
    assert "admins_online" in site


async def test_format_app_metrics_message_renders_sections(db_session_maker):
    """Карточка /metrics содержит обе секции и корректно переживает пустую БД."""
    from bots.telegram.app_metrics import collect_app_metrics, format_app_metrics_message

    metrics = await collect_app_metrics()
    text = format_app_metrics_message(metrics)

    assert "Показатели сайта и бота" in text
    assert "Веб-панель" in text
    assert "VK-бот" in text
    assert "Администраторов онлайн" in text
    assert "Общие (без отдела)" in text
    assert "Очередь Outbox" in text
    assert "Время" in text
    # HTML должен быть валиден по смыслу: все теги закрыты.
    assert text.count("<b>") == text.count("</b>")


async def test_metrics_keyboard_and_status_toggle():
    """Клавиатуры содержат переключатель вида и кнопку обновления."""
    from bots.telegram.keyboards import (
        STATUS_VIEW_APP,
        STATUS_VIEW_INFRA,
        get_metrics_inline_keyboard,
        get_status_inline_keyboard,
    )

    infra_kb = get_status_inline_keyboard(None, view=STATUS_VIEW_INFRA)
    infra_callbacks = [b.callback_data for row in infra_kb.inline_keyboard for b in row]
    # Активный срез помечен галочкой и не переключается повторно.
    assert "status:noop" in infra_callbacks
    assert "status:view:app" in infra_callbacks
    # Кнопка немедленного обновления присутствует в обоих срезах.
    assert "status:refresh" in infra_callbacks

    app_kb = get_status_inline_keyboard(None, view=STATUS_VIEW_APP)
    app_callbacks = [b.callback_data for row in app_kb.inline_keyboard for b in row]
    assert "status:view:infra" in app_callbacks
    assert "status:refresh" in app_callbacks

    metrics_kb = get_metrics_inline_keyboard(None)
    metrics_callbacks = [b.callback_data for row in metrics_kb.inline_keyboard for b in row]
    assert "metrics:refresh" in metrics_callbacks


def test_main_keyboard_has_statistics_button():
    """Кнопка «📈 Статистика» добавлена в главное меню бота."""
    from bots.telegram.keyboards import get_main_reply_keyboard

    kb = get_main_reply_keyboard(webapp_url=None)
    texts = [btn.text for row in kb.keyboard for btn in row]
    assert "📈 Статистика" in texts


async def test_render_status_content_app_view(db_session_maker):
    """Карточка статуса в режиме «показатели» отдаёт бизнес-метрики."""
    import unittest.mock as mock

    from bots.telegram.docker_client import DockerClient
    from bots.telegram.handlers.status import render_status_content
    from bots.telegram.keyboards import STATUS_VIEW_APP, STATUS_VIEW_INFRA

    docker_mock = mock.AsyncMock(spec=DockerClient)
    docker_mock.list_containers.return_value = []

    infra_text, _ = await render_status_content(docker_mock, view=STATUS_VIEW_INFRA)
    assert "Состояние сервера" in infra_text

    app_text, kb = await render_status_content(docker_mock, view=STATUS_VIEW_APP)
    assert "Показатели сайта и бота" in app_text
    assert "Администраторов онлайн" in app_text
    # Переключатель вида сохраняется, чтобы можно было вернуться к железу.
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "status:view:infra" in callbacks
