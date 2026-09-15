"""Тесты конфигурации."""

from zoneinfo import ZoneInfo

import pytest

from core.config import settings
from core.reporting import get_app_tz


def test_database_url_uses_asyncpg():
    assert settings.database_url.startswith("postgresql+asyncpg://")


def test_app_timezone_default_is_moscow():
    assert settings.APP_TIMEZONE == "Europe/Moscow"


def test_get_app_tz_returns_valid_zone():
    tz = get_app_tz()
    assert isinstance(tz, ZoneInfo)
    assert str(tz) == "Europe/Moscow"


def test_allow_db_create_disabled_by_default():
    # В production приложение не должно само создавать базу
    assert settings.ALLOW_DB_CREATE is False


def test_report_time_format():
    assert ":" in settings.REPORT_TIME
    hours, minutes = settings.REPORT_TIME.split(":")
    assert 0 <= int(hours) <= 23
    assert 0 <= int(minutes) <= 59


def test_ensure_production_config_dev_no_op():
    """В dev-окружении проверка production не должна бросать исключение."""
    settings.ensure_production_config()


def test_dev_environment_is_not_production():
    assert settings.IS_PRODUCTION is False


def test_trusted_proxies_empty_by_default():
    assert set() == settings.TRUSTED_PROXIES


def test_session_https_only_default_false():
    assert settings.SESSION_HTTPS_ONLY is False


# --- Ошибка #17: строгая и прозрачная валидация конфигурации ---


def _settings_env_only(monkeypatch):
    """Класс Settings, читающий ТОЛЬКО os.environ (без файла .env).

    settings_customise_sources жёстко подключает файл ".env", поэтому для
    изоляции теста от локального .env подменяем список источников: init +
    тот же LenientEnvSource, что и в production (без dotenv-источника).
    """
    from core.config import Settings, _LenientEnvSource

    def _sources(
        cls,
        settings_cls: type,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (init_settings, _LenientEnvSource(settings_cls), file_secret_settings)

    monkeypatch.setattr(Settings, "settings_customise_sources", classmethod(_sources))
    return Settings


def test_vk_bot_token_is_not_mocked(monkeypatch):
    """Фиктивный 'mock_token' больше не подставляется: поле остаётся None."""
    Settings = _settings_env_only(monkeypatch)

    monkeypatch.delenv("VK_BOT_TOKEN", raising=False)
    fresh = Settings()
    assert fresh.VK_BOT_TOKEN is None
    assert fresh.VK_BOT_TOKEN != "mock_token"


def test_admin_vk_ids_is_not_mocked(monkeypatch):
    """Фиктивный набор {1} больше не подставляется: остаётся пустое множество."""
    Settings = _settings_env_only(monkeypatch)

    monkeypatch.delenv("ADMIN_VK_IDS", raising=False)
    fresh = Settings()
    assert not fresh.ADMIN_VK_IDS
    assert 1 not in fresh.ADMIN_VK_IDS


def test_validate_required_fails_without_required_env(monkeypatch):
    """При отсутствии обязательных параметров валидация падает с диагностикой.

    RuntimeError перечисляет ВСЕ отсутствующие обязательные параметры
    (креды PostgreSQL, VK_BOT_TOKEN, ADMIN_VK_IDS) — приложение обязано
    падать на этапе инициализации с понятным сообщением.
    """
    Settings = _settings_env_only(monkeypatch)

    for name in (
        "POSTGRES_USER",
        "DB_USER",
        "POSTGRES_PASSWORD",
        "DB_PASS",
        "POSTGRES_PASS",
        "VK_BOT_TOKEN",
        "ADMIN_VK_IDS",
    ):
        monkeypatch.delenv(name, raising=False)
    fresh = Settings()

    with pytest.raises(RuntimeError) as exc_info:
        fresh.validate_required()

    message = str(exc_info.value)
    for fragment in ("POSTGRES_USER", "POSTGRES_PASSWORD", "VK_BOT_TOKEN", "ADMIN_VK_IDS"):
        assert fragment in message


def test_validate_required_passes_with_full_config(monkeypatch):
    """Полная конфигурация проходит validate_required() без исключений."""
    Settings = _settings_env_only(monkeypatch)

    monkeypatch.delenv("VK_BOT_TOKEN", raising=False)
    monkeypatch.setenv("POSTGRES_USER", "bot_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "strong_password_123")
    monkeypatch.setenv("VK_BOT_TOKEN", "vk_token_value")
    monkeypatch.setenv("ADMIN_VK_IDS", "123456789,987654321")

    fresh = Settings()
    # Не должно бросать исключение
    fresh.validate_required()
