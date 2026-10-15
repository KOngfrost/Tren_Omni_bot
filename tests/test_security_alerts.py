"""Тесты системы безопасности: алерты в Telegram, тримминг логина, блокировка зондов."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.security_alerts import _memory_cooldowns, _memory_suppressed, send_security_alert
from web.security.middleware import SUSPICIOUS_PROBE_REGEX


class TestSecurityAlerts:
    """Тесты отправки Telegram-предупреждений об инцидентах ИБ."""

    @pytest.fixture(autouse=True)
    def reset_cooldown_memory(self):
        """Очищаем in-memory cooldown перед каждым тестом."""
        _memory_cooldowns.clear()
        _memory_suppressed.clear()
        yield
        _memory_cooldowns.clear()
        _memory_suppressed.clear()

    async def test_send_security_alert_telegram_success(self, monkeypatch):
        """Успешная отправка алерта в Telegram при корректных настройках."""
        from core.config import settings

        monkeypatch.setattr(settings, "TELEGRAM_ALERTS_ENABLED", True)
        monkeypatch.setattr(
            settings, "TELEGRAM_BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        )
        monkeypatch.setattr(settings, "TELEGRAM_ADMIN_ID", 999888777)
        monkeypatch.setattr(settings, "VK_REPORT_ADMIN_ID", 0)

        mock_post = AsyncMock()
        mock_response = MagicMock(status_code=200, text="ok")
        mock_post.return_value = mock_response

        with patch("httpx.AsyncClient.post", mock_post):
            sent = await send_security_alert(
                event_type="test_alert",
                title="Тестовое предупреждение",
                details={
                    "ip": "1.2.3.4",
                    "username": "attacker",
                    "path": "/auth/login",
                    "reason": "Тестовая атака",
                },
            )

        assert sent is True
        assert mock_post.called
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["chat_id"] == 999888777
        assert "attacker" in call_kwargs["json"]["text"]
        assert "1.2.3.4" in call_kwargs["json"]["text"]

    async def test_cooldown_anti_spam_suppression(self, monkeypatch):
        """Повторные алерты для одного IP и типа события подавляются кулдауном."""
        from core.config import settings

        monkeypatch.setattr(settings, "TELEGRAM_ALERTS_ENABLED", True)
        monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "fake_token")
        monkeypatch.setattr(settings, "TELEGRAM_ADMIN_ID", 12345)
        monkeypatch.setattr(settings, "VK_REPORT_ADMIN_ID", 0)

        mock_post = AsyncMock(return_value=MagicMock(status_code=200))

        with patch("httpx.AsyncClient.post", mock_post):
            # Первый алерт — должен отправиться
            sent_1 = await send_security_alert(
                event_type="bruteforce",
                title="Атака",
                details={"ip": "10.0.0.1"},
            )
            assert sent_1 is True
            assert mock_post.call_count == 1

            # Второй алерт через 1 секунду — должен быть подавлен
            sent_2 = await send_security_alert(
                event_type="bruteforce",
                title="Атака",
                details={"ip": "10.0.0.1"},
            )
            assert sent_2 is False
            # Количество вызовов Telegram API не должно увеличиться
            assert mock_post.call_count == 1

    async def test_probe_regex_matches_scanner_patterns(self):
        """Проверка регулярного выражения перехвата вредоносных зондов."""
        assert SUSPICIOUS_PROBE_REGEX.search("/.env")
        assert SUSPICIOUS_PROBE_REGEX.search("/.git/config")
        assert SUSPICIOUS_PROBE_REGEX.search("/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin.php")
        assert SUSPICIOUS_PROBE_REGEX.search("/phpinfo.php")
        assert SUSPICIOUS_PROBE_REGEX.search("/public/vendor/laravel-filemanager/js/script.js")
        assert SUSPICIOUS_PROBE_REGEX.search("/wp-admin/setup-config.php")
        assert SUSPICIOUS_PROBE_REGEX.search(
            "/.config/gcloud/application_default_credentials.json"
        )

        # Легитимные пути приложения не должны матчиться
        assert not SUSPICIOUS_PROBE_REGEX.search("/auth/login")
        assert not SUSPICIOUS_PROBE_REGEX.search("/tickets/12")
        assert not SUSPICIOUS_PROBE_REGEX.search("/api/v1/departments")
        assert not SUSPICIOUS_PROBE_REGEX.search("/static/js/app.js")

    async def test_load_ticket_idor_triggers_security_alert(self, monkeypatch):
        """Попытка доступа к тикету чужого отдела вызывает 403 и отправляет security-алерт."""
        from fastapi import HTTPException

        from core.models import Ticket
        from web.routes.tickets import _load_ticket_for_user

        fake_ticket = MagicMock(spec=Ticket)
        fake_ticket.id = 12
        fake_ticket.department_id = 99  # Другой отдел

        async def fake_get_ticket(ticket_id):
            return fake_ticket

        async def fake_get_admin_scope(session, user):
            # is_super=False, dept_id=1
            return False, 1

        alert_sent = []

        async def mock_alert(**kwargs):
            alert_sent.append(kwargs)
            return True

        monkeypatch.setattr("web.routes.tickets._get_ticket", fake_get_ticket)
        monkeypatch.setattr("web.routes.tickets.get_admin_scope", fake_get_admin_scope)
        monkeypatch.setattr("core.security_alerts.send_security_alert", mock_alert)

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.50"
        mock_request.headers = {}

        with pytest.raises(HTTPException) as exc_info:
            await _load_ticket_for_user(
                ticket_id=12,
                user={"username": "dept_admin_2", "role": "DEPARTMENT_ADMIN"},
                request=mock_request,
            )

        assert exc_info.value.status_code == 403
        assert len(alert_sent) == 1
        assert alert_sent[0]["event_type"] == "idor_attempt"
        assert alert_sent[0]["details"]["username"] == "dept_admin_2"
        assert alert_sent[0]["details"]["path"] == "/tickets/12"


class TestLoginTrimming:
    """Тест санитизации (trimming) имени пользователя при входе."""

    def test_login_trims_username(self, web_client, monkeypatch):
        """Пробелы по краям логина автоматически отсекаются (.strip())."""
        import re

        from web.routes import auth

        captured_username = None

        async def fake_authenticate(session, username, password):
            nonlocal captured_username
            captured_username = username
            return None

        monkeypatch.setattr(auth, "_authenticate", fake_authenticate)

        login_page = web_client.get("/auth/login")
        csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text).group(1)

        web_client.post(
            "/auth/login",
            data={
                "username": "   Yenotick   ",
                "password": "some_password",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )

        assert captured_username == "Yenotick"


class TestProbeFilterMiddleware:
    """Тесты перехвата и блокировки зондов сканеров на уровне FastAPI."""

    def test_blocks_env_probe(self, web_client):
        """Обращение к /.env возвращает 404 и блокируется."""
        response = web_client.get("/.env")
        assert response.status_code == 404

    def test_blocks_git_probe(self, web_client):
        """Обращение к /.git/config возвращает 404 и блокируется."""
        response = web_client.get("/.git/config")
        assert response.status_code == 404

    def test_blocks_phpunit_rce_probe(self, web_client):
        """POST на phpunit eval-stdin возвращает 404 и блокируется."""
        response = web_client.post("/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin.php")
        assert response.status_code == 404
