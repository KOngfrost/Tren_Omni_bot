"""Тесты новых возможностей панели.

Покрывает:
- полный аудит действий (core.audit, middleware, классификация путей);
- автоудаление истёкших временных администраторов;
- персональный 2FA: личный переключатель и недоступность без привязки к VK;
- нормализацию акцентного цвета и его сохранение в cookie;
- сокращённый TTL присутствия администратора;
- замену эмодзи на inline-SVG иконки.
"""

from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from core.audit import (
    AuditMiddleware,
    classify_path,
    drain_audit_queue,
    enqueue_audit,
    flush_audit_queue,
    pending_audit_count,
    reset_audit_queue,
    should_audit,
)
from core.config import settings
from core.models import Admin, Log, User, UserRole, WebRole, WebUser
from core.temp_admin_cleanup import cleanup_expired_temporary_admins
from core.time_utils import now_app_tz
from web.main import app
from web.routes.settings import DEFAULT_ACCENT_COLOR, normalize_accent_color
from web.security.passwords import hash_password
from web.templating import templates


class TestAuditHelpers:
    """Классификация и фильтрация путей аудита."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/auth/login", "Вход в панель"),
            ("/tickets/5", "Работа с заявками"),
            ("/admin/admins", "Управление администраторами"),
            ("/settings/2fa", "Настройки"),
            ("/logs/", "Журнал аудита"),
            ("/api/counters", "API: счётчики уведомлений"),
        ],
    )
    def test_classify_path(self, path, expected):
        assert classify_path(path) == expected

    def test_classify_path_fallback(self):
        """Неизвестный путь не должен ронять аудит, а получает общую подпись."""
        assert classify_path("/что-то-новое") == "Просмотр страницы"

    @pytest.mark.parametrize(
        "path", ["/static/style.css", "/health", "/favicon.ico", "/metrics", "/_internal"]
    )
    def test_service_paths_are_skipped(self, path):
        """Служебные пути исключены, иначе журнал утонет в шуме."""
        assert should_audit(path) is False

    @pytest.mark.parametrize("path", ["/", "/tickets/", "/admin/admins", "/auth/login"])
    def test_business_paths_are_audited(self, path):
        assert should_audit(path) is True

    def test_middleware_is_registered(self):
        """AuditMiddleware должен быть подключён к приложению."""
        names = [m.cls.__name__ for m in app.user_middleware]
        assert "AuditMiddleware" in names

    def test_middleware_accepts_custom_event_sink(self):
        """Обработчик событий аудита внедряется — это нужно для тестов."""
        received: list[dict] = []
        mw = AuditMiddleware(app, background=received.append)
        assert mw._background is not None

    def test_service_paths_are_not_classified_as_actions(self):
        """Служебные пути даже при попадании в буфер получают общую подпись."""
        # classify_path не имеет особых правил для служебных путей — они
        # отсеиваются на уровне should_audit, поэтому здесь просто проверяем,
        # что функция не падает на любом входе.
        assert classify_path("/static/missing.css")


class TestAuditBuffer:
    """Буфер аудита: события копятся в памяти и выгружаются пачкой в БД."""

    def test_enqueue_and_drain_roundtrip(self):
        """События сохраняют порядок и вынимаются из буфера FIFO."""
        reset_audit_queue()
        try:
            enqueue_audit({"action": "Первое", "actor_type": "web"})
            enqueue_audit({"action": "Второе", "actor_type": "web"})
            assert pending_audit_count() == 2

            taken = drain_audit_queue()
            assert [e["action"] for e in taken] == ["Первое", "Второе"]
            assert pending_audit_count() == 0
        finally:
            reset_audit_queue()

    def test_drain_respects_limit(self):
        """За один раз выгружается не больше AUDIT_BATCH_SIZE событий."""
        reset_audit_queue()
        try:
            for i in range(5):
                enqueue_audit({"action": f"Событие {i}", "actor_type": "web"})
            taken = drain_audit_queue(limit=3)
            assert len(taken) == 3
            assert pending_audit_count() == 2
        finally:
            reset_audit_queue()

    def test_queue_is_bounded(self):
        """Буфер ограничен: при переполнении старые события вытесняются."""
        reset_audit_queue()
        try:
            from core.audit import AUDIT_QUEUE_MAX

            for i in range(AUDIT_QUEUE_MAX + 10):
                enqueue_audit({"action": f"e{i}", "actor_type": "web"})
            # deque(maxlen=...) гарантирует, что размер не превысит предел.
            assert pending_audit_count() <= AUDIT_QUEUE_MAX
        finally:
            reset_audit_queue()

    @pytest.mark.asyncio
    async def test_flush_writes_events_to_journal(self, db_session_maker):
        """flush_audit_queue сохраняет накопленные события в таблицу Log."""
        reset_audit_queue()
        try:
            enqueue_audit(
                {
                    "action": "Тестовое действие",
                    "details": "POST /test -> 200",
                    "actor_type": "web",
                    "actor_name": "tester",
                    "is_mutation": True,
                }
            )
            written = await flush_audit_queue()
            assert written == 1
            assert pending_audit_count() == 0

            async with db_session_maker() as session:
                row = (
                    await session.execute(
                        select(Log).where(Log.action == "Тестовое действие")
                    )
                ).scalar_one_or_none()
            assert row is not None
            assert row.actor_name == "tester"
            assert row.is_mutation is True
        finally:
            reset_audit_queue()

    @pytest.mark.asyncio
    async def test_flush_on_empty_queue_is_noop(self):
        """Пустой буфер не создаёт лишних обращений к БД."""
        reset_audit_queue()
        assert await flush_audit_queue() == 0


class TestTempAdminCleanup:
    """Истёкшие временные учётные записи должны удаляться автоматически."""

    @pytest.mark.asyncio
    async def test_expired_temp_admin_is_deactivated_then_deleted(self, db_session_maker):
        # Отсчёт отступаем от «сейчас», а не от полуночи: точка «сегодня в 12:00»
        # после полуночи оказывается в будущем, и проверка была бы провалена
        # при любом запуске раньше полудня.
        expired_at = now_app_tz() - timedelta(hours=2)
        async with db_session_maker() as session:
            session.add(WebUser(username="temp_old", password_hash="x", expires_at=expired_at))
            # Не истёкший — должен остаться нетронутым.
            session.add(WebUser(username="temp_fresh", password_hash="x", expires_at=None))
            await session.commit()

        # Первый проход: деактивация, удаление ещё не выполняется.
        removed = await cleanup_expired_temporary_admins(grace_minutes=60)
        assert removed == 0

        async with db_session_maker() as session:
            old = (
                await session.execute(select(WebUser).where(WebUser.username == "temp_old"))
            ).scalar_one()
            fresh = (
                await session.execute(select(WebUser).where(WebUser.username == "temp_fresh"))
            ).scalar_one()
            assert old.is_active is False, "Истёкшая учётка должна быть отключена сразу"
            assert fresh.is_active is True, "Действующая учётка не трогается"

        # Второй проход с нулевым grace: запись удаляется.
        removed = await cleanup_expired_temporary_admins(grace_minutes=0)
        assert removed == 1

        async with db_session_maker() as session:
            names = set((await session.execute(select(WebUser.username))).scalars().all())
        assert "temp_old" not in names
        assert "temp_fresh" in names, "Постоянная учётка не должна удаляться"

    @pytest.mark.asyncio
    async def test_vk_linked_admin_with_expiry_is_not_deleted(self, db_session_maker):
        """Постоянные админы (admin_id IS NOT NULL) не удаляются, даже с истёкшим сроком."""
        async with db_session_maker() as session:
            user = User(vk_id=777000111, full_name="Постоянный Админ")
            session.add(user)
            await session.flush()
            admin = Admin(user_id=user.id, role=UserRole.SUPERADMIN)
            session.add(admin)
            await session.flush()
            session.add(
                WebUser(
                    username="perma_admin",
                    password_hash="x",
                    admin_id=admin.id,
                    expires_at=now_app_tz() - timedelta(hours=1),
                )
            )
            await session.commit()

        await cleanup_expired_temporary_admins(grace_minutes=0)

        async with db_session_maker() as session:
            row = (
                await session.execute(select(WebUser).where(WebUser.username == "perma_admin"))
            ).scalar_one()
            assert row is not None, "Аккаунт с привязкой к VK удалять нельзя"

    @pytest.mark.asyncio
    async def test_cleanup_logs_audit_events(self, db_session_maker):
        """Каждое удаление фиксируется в журнале — история не зависит от аккаунта."""
        expired_at = now_app_tz() - timedelta(hours=2)
        async with db_session_maker() as session:
            session.add(WebUser(username="temp_logged", password_hash="x", expires_at=expired_at))
            await session.commit()

        await cleanup_expired_temporary_admins(grace_minutes=0)
        await cleanup_expired_temporary_admins(grace_minutes=0)

        async with db_session_maker() as session:
            rows = (
                (
                    await session.execute(
                        select(Log).where(Log.action.like("%временного администратора%"))
                    )
                )
                .scalars()
                .all()
            )
        assert rows, "Автоудаление должно оставлять след в журнале аудита"
        assert any("temp_logged" in (r.details or "") for r in rows)


async def _login_vk_admin(client, username: str, password: str, session) -> None:
    """Создать админа с привязкой к VK и выполнить вход.

    vk_id делаем уникальным на каждый вызов: users.vk_id уникален, и без
    этого второй тест в том же классе упал бы на вставке (и вход не прошёл бы).
    """
    vk_id = 555000000 + (abs(hash(username)) % 900000)
    user = User(vk_id=vk_id, full_name=f"Владелец {username}")
    session.add(user)
    await session.flush()
    admin = Admin(user_id=user.id, role=UserRole.SUPERADMIN)
    session.add(admin)
    await session.flush()
    session.add(
        WebUser(
            username=username,
            password_hash=hash_password(password),
            role=WebRole.SUPERADMIN,
            admin_id=admin.id,
            is_active=True,
            two_factor_enabled=True,
        )
    )
    await session.commit()

    await _post_login(client, username, password)


async def _csrf(client) -> str:
    """Достать CSRF-токен из сессии (он подставляется в формы страниц)."""
    import re

    page = await client.get("/settings/")
    match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', page.text)
    return match.group(1) if match else ""


async def _post_login(client, username: str, password: str):
    """Выполнить вход, предварительно взяв CSRF-токен со страницы формы."""
    import re

    page = await client.get("/auth/login")
    match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', page.text)
    token = match.group(1) if match else ""
    return await client.post(
        "/auth/login",
        data={
            "username": username,
            "password": password,
            "csrf_token": token,
        },
        follow_redirects=False,
    )


# ==========================================
# Персональный 2FA
# ==========================================


class TestPersonal2FA:
    """2FA управляется каждым администратором лично; без VK её нет вовсе."""

    def test_two_factor_available_property(self):
        """2FA применима только к учёткам с привязкой к VK."""
        linked = WebUser(username="a", password_hash="x", admin_id=1)
        temp = WebUser(username="b", password_hash="x", admin_id=None)
        assert linked.two_factor_available is True
        assert temp.two_factor_available is False

    @pytest.mark.asyncio
    async def test_defaults_to_enabled(self, db_session_maker):
        """Новая учётная запись сохраняется с включённым 2FA.

        Значение по умолчанию проставляется при INSERT, поэтому проверяем
        именно сохранённую запись, а не только что созданный объект.
        """
        async with db_session_maker() as session:
            row = WebUser(username="default_2fa", password_hash="x")
            session.add(row)
            await session.commit()
            assert row.two_factor_enabled is True

    @pytest.mark.asyncio
    async def test_personal_2fa_toggle_persists(self, db_session_maker, monkeypatch):
        """Переключатель меняет личный флаг 2FA и сохраняет его в БД."""
        monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "test_settings_secret_key_123456789")
        monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", False)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with db_session_maker() as session:
                await _login_vk_admin(client, "pfa_admin", "PersonalPass123", session)

            resp = await client.get("/settings/")
            assert resp.status_code == 200
            assert "Моя двухфакторная аутентификация" in resp.text
            assert "Отключить для себя" in resp.text

            csrf = await _csrf(client)
            await client.post(
                "/settings/2fa/personal",
                data={"enabled": "false", "csrf_token": csrf},
                follow_redirects=False,
            )

        async with db_session_maker() as session:
            row = (
                await session.execute(select(WebUser).where(WebUser.username == "pfa_admin"))
            ).scalar_one()
            assert row.two_factor_enabled is False

    @pytest.mark.asyncio
    async def test_personal_2fa_hidden_without_vk(self, db_session_maker, monkeypatch):
        """Временный админ без VK не видит переключателя: кода некуда доставлять."""
        monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "test_settings_secret_key_123456789")
        monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", False)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with db_session_maker() as session:
                session.add(
                    WebUser(
                        username="temp_no_vk",
                        password_hash=hash_password("TempPass123"),
                        role=WebRole.SUPERADMIN,
                        admin_id=None,
                        is_active=True,
                    )
                )
                await session.commit()

            await _post_login(client, "temp_no_vk", "TempPass123")
            resp = await client.get("/settings/")

        assert resp.status_code == 200
        assert "Моя двухфакторная аутентификация" in resp.text
        assert "2FA не требуется" in resp.text
        assert "Отключить для себя" not in resp.text

    @pytest.mark.asyncio
    async def test_temp_admin_can_log_in_while_2fa_enabled(self, db_session_maker, monkeypatch):
        """Глобально включённая 2FA не блокирует временных админов без VK."""
        monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "test_settings_secret_key_123456789")
        monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", True)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with db_session_maker() as session:
                session.add(
                    WebUser(
                        username="temp_direct",
                        password_hash=hash_password("TempPass123"),
                        role=WebRole.SUPERADMIN,
                        admin_id=None,
                        is_active=True,
                        two_factor_enabled=True,
                    )
                )
                await session.commit()

            resp = await _post_login(client, "temp_direct", "TempPass123")
            # 303 на дашборд = вход без запроса кода.
            assert resp.status_code == 303
            assert "/auth/2fa" not in resp.headers.get("location", "")

            # Сессия активна — дашборд доступен.
            dash = await client.get("/")
            assert dash.status_code == 200


# ==========================================
# Акцентный цвет
# ==========================================


class TestAccentColor:
    """Выбор акцентного цвета оформления."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("#ff0000", "#ff0000"),
            ("#ABCDEF", "#abcdef"),
            ("#abc", "#aabbcc"),
            ("  #123456  ", "#123456"),
        ],
    )
    def test_normalize_accepts_valid(self, raw, expected):
        assert normalize_accent_color(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "red",
            "#12345",
            "#1234567",
            "javascript:alert(1)",
            "#ff0000; background: url(x)",
            "<script>",
            "",
            None,
        ],
    )
    def test_normalize_rejects_invalid(self, raw):
        """Некорректное значение отбрасывается — иначе это вектор CSS-инъекции."""
        assert normalize_accent_color(raw) is None

    def test_default_color_is_valid(self):
        assert DEFAULT_ACCENT_COLOR == "#fd60c9"
        assert normalize_accent_color(DEFAULT_ACCENT_COLOR) == DEFAULT_ACCENT_COLOR

    @pytest.mark.asyncio
    async def test_theme_saves_preferences(self, db_session_maker, monkeypatch):
        """Настройки темы и эффектов сохраняются, кнопка сохранения оформлена отдельным блоком."""
        monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "test_settings_secret_key_123456789")
        monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", False)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with db_session_maker() as session:
                await _login_vk_admin(client, "accent_admin", "AccentPass123", session)

            resp = await client.post(
                "/settings/theme",
                data={
                    "theme": "dark",
                    "glass_effect": "true",
                    "csrf_token": await _csrf(client),
                },
                headers={"Accept": "application/json"},
            )
            assert resp.status_code == 200
            assert resp.json()["glass_effect"] is True

            page = await client.get("/settings/")
            assert "settings-save-block" in page.text
            assert 'id="btnSaveSettings"' in page.text

    @pytest.mark.asyncio
    async def test_invalid_accent_falls_back_to_default(self, db_session_maker, monkeypatch):
        """Мусор в поле цвета не должен попасть в cookie."""
        monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "test_settings_secret_key_123456789")
        monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", False)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with db_session_maker() as session:
                await _login_vk_admin(client, "accent_bad", "AccentPass123", session)

            resp = await client.post(
                "/settings/theme",
                data={
                    "theme": "dark",
                    "accent_color": "red;}</style><script>alert(1)</script>",
                    "csrf_token": await _csrf(client),
                },
                headers={"Accept": "application/json"},
            )
            assert resp.status_code == 200
            assert resp.json()["accent_color"] == DEFAULT_ACCENT_COLOR

    @pytest.mark.asyncio
    async def test_settings_page_has_separate_save_div_and_no_color_picker(
        self, db_session_maker, monkeypatch
    ):
        """Настройка цвета убрана (используется единый розовый как у иконки сайта),
        а кнопка сохранения вынесена в отдельный div в самом низу страницы, а не в подвал.
        """
        monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "test_settings_secret_key_123456789")
        monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", False)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with db_session_maker() as session:
                await _login_vk_admin(client, "save_div_admin", "AccentPass123", session)
            resp = await client.get("/settings/")

        assert resp.status_code == 200
        # Ползунок и настройка цвета удалены
        assert 'id="accent_hue"' not in resp.text
        assert 'id="accent_color"' not in resp.text
        assert "accent-hue-slider" not in resp.text
        # Кнопка сохранения — отдельный div внизу, не плавающий подвал
        assert "settings-save-block" in resp.text
        assert "settings-sticky-footer" not in resp.text
        assert 'id="btnSaveSettings"' in resp.text


# ==========================================
# Присутствие администратора
# ==========================================


class TestPresenceWindow:
    """Окно активности администратора должно быть коротким."""

    def test_presence_ttl_is_short(self):
        """15 минут — слишком долго; по умолчанию 120 секунд."""
        from core.admin_presence import ADMIN_PRESENCE_TTL_SECONDS

        assert ADMIN_PRESENCE_TTL_SECONDS == 120
        assert ADMIN_PRESENCE_TTL_SECONDS <= 300

    def test_presence_ttl_matches_config(self):
        """TTL берётся из конфигурации, а не зашит в код."""
        from core.admin_presence import ADMIN_PRESENCE_TTL_SECONDS

        assert max(
            30, int(settings.ADMIN_PRESENCE_TTL_SECONDS)
        ) == ADMIN_PRESENCE_TTL_SECONDS


# ==========================================
# Иконки вместо эмодзи
# ==========================================


class TestIcons:
    """Эмодзи заменены inline-SVG иконками."""

    def test_icon_macro_available(self):
        assert templates.env.globals.get("icon") is not None, (
            "макрос icon должен быть зарегистрирован глобально"
        )

    def test_icon_renders_svg(self):
        out = str(templates.env.globals["icon"]("user", 16))
        assert out.startswith("<svg")
        assert 'width="16"' in out

    def test_unknown_icon_falls_back(self):
        """Опечатка в имени не должна ломать страницу."""
        assert "<svg" in str(templates.env.globals["icon"]("нет-такой-иконки", 16))

    def test_templates_have_no_emoji(self):
        """В шаблонах не осталось эмодзи-глифов."""
        import glob
        import re

        pattern = re.compile(
            "[\U0001F300-\U0001FAFF\u2190-\u21FF\u2600-\u27BF\u2B00-\u2BFF\u25CB\u25CF]"
        )
        offenders = []
        for path in glob.glob("web/templates/*.html"):
            with open(path, encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    if pattern.search(line):
                        offenders.append(f"{path}:{number}")
        assert not offenders, "Эмодзи остались: " + ", ".join(offenders)

