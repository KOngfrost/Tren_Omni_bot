import contextlib
import os
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, field_validator
from pydantic_core import PydanticUndefined
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

load_dotenv()

from core import PROJECT_VERSION as _project_version  # noqa: E402

# Файл dev-фоллбэка секрета сессий (добавлен в .gitignore)
_DEV_SECRET_FILE = Path(__file__).resolve().parent.parent / ".session_secret"


def _load_or_create_dev_secret() -> str:
    """Dev-фоллбэк секрета сессий: файл .session_secret в корне проекта.

    Секрет генерируется один раз и сохраняется на диск, чтобы сессии
    веб-панели переживали перезапуск процесса в development-режиме.
    В production секрет обязателен в .env — ensure_production_config()
    прервёт запуск без него.
    """
    # Читаем существующий секрет; FileNotFoundError/OSError — создадим новый
    with contextlib.suppress(OSError):
        value = _DEV_SECRET_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(64)
    # Нет прав на запись — секрет будет новым при каждом запуске (только dev)
    with contextlib.suppress(OSError):
        _DEV_SECRET_FILE.write_text(value, encoding="utf-8")
    return value


class _LenientEnvSource(EnvSettingsSource):
    """Env-источник, который не падает на не-JSON строках для списков и множеств."""

    def decode_complex_value(self, field_name: str, field: Any, value: Any) -> Any:
        try:
            return super().decode_complex_value(field_name, field, value)
        except Exception:
            return value


class _LenientDotEnvSource(DotEnvSettingsSource):
    """DotEnv-источник, который не падает на не-JSON строках для списков и множеств."""

    def decode_complex_value(self, field_name: str, field: Any, value: Any) -> Any:
        try:
            return super().decode_complex_value(field_name, field, value)
        except Exception:
            return value


class Settings(BaseSettings):
    """Единый класс настроек приложения на базе Pydantic BaseSettings."""

    PROJECT_VERSION: str = _project_version

    # === Окружение ===
    APP_ENV: str = "development"
    dev_environments: set[str] = {"", "development", "dev", "test", "testing", "local"}

    @property
    def IS_PRODUCTION(self) -> bool:
        return self.APP_ENV not in self.dev_environments

    # Database
    DB_USER: str = Field(default="", validation_alias=AliasChoices("POSTGRES_USER", "DB_USER"))
    DB_PASS: str = Field(
        default="", validation_alias=AliasChoices("POSTGRES_PASSWORD", "DB_PASS", "POSTGRES_PASS")
    )
    DB_NAME: str = Field(
        default="oss_bot", validation_alias=AliasChoices("POSTGRES_DB", "DB_NAME")
    )
    DB_HOST: str | None = None
    DB_PORT: str = "5432"

    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800
    DB_POOL_PRE_PING: bool = True
    DB_USE_PGBOUNCER: bool = False
    DB_STATEMENT_CACHE_SIZE: int = 1024

    @property
    def db_host(self) -> str:
        if self.DB_HOST:
            return self.DB_HOST
        return "db" if self.IS_PRODUCTION else "localhost"

    @property
    def database_url(self) -> str:
        if not self.DB_USER or not self.DB_PASS:
            return ""
        _db_user = quote_plus(self.DB_USER)
        _db_pass = quote_plus(self.DB_PASS)
        return f"postgresql+asyncpg://{_db_user}:{_db_pass}@{self.db_host}:{self.DB_PORT}/{self.DB_NAME}"

    # VK
    VK_MODE: str = "longpoll"
    VK_CONFIRMATION_TOKEN: str = ""
    VK_CALLBACK_SECRET: str = ""
    VK_BOT_TOKEN: str | None = None
    ADMIN_VK_IDS: set[int] = Field(default_factory=set)
    VK_REPORT_ADMIN_ID: int = 0
    REPORT_TIME: str = "09:00"
    APP_TIMEZONE: str = "Europe/Moscow"
    ALLOW_DB_CREATE: bool = False

    # Security & Web Admin
    SESSION_SECRET_KEY: str = ""

    @property
    def session_secret_key(self) -> str:
        if self.SESSION_SECRET_KEY:
            return self.SESSION_SECRET_KEY
        return _load_or_create_dev_secret()

    SESSION_HTTPS_ONLY: bool = False
    TRUSTED_PROXIES: set[str] = Field(default_factory=set)
    WEB_ADMIN_USERNAME: str = ""
    WEB_ADMIN_PASSWORD: str = ""
    CORS_ORIGINS: list[str] = Field(default_factory=lambda: ["http://localhost:8000"])
    WEB_OUTBOX_WORKER: bool = True

    # Redis
    REDIS_URL: str | None = None
    CACHE_DEFAULT_TTL: int = 300
    SESSION_TTL: int = 86400

    # Sentry
    SENTRY_DSN: str | None = None
    SENTRY_ENVIRONMENT: str = "development"
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1

    # 2FA & Bootstrap
    TWO_FACTOR_ENABLED: bool = True
    TWO_FACTOR_CODE_TTL: int = 300
    BOOTSTRAP_ALLOWED: bool = True
    FORCE_BOOTSTRAP_OVERRIDE: bool = False

    # SMTP
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    REPORT_EMAILS: list[str] = Field(default_factory=list)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            _LenientEnvSource(settings_cls),
            _LenientDotEnvSource(settings_cls, env_file=".env", env_file_encoding="utf-8"),
            file_secret_settings,
        )

    @field_validator("ADMIN_VK_IDS", mode="before")
    @classmethod
    def _parse_admin_vk_ids(cls, v: Any) -> set[int]:
        if isinstance(v, set):
            return v
        if isinstance(v, int):
            return {v}
        if isinstance(v, str):
            return {int(x.strip()) for x in v.split(",") if x.strip().isdigit()}
        if isinstance(v, (list, tuple)):
            return {int(x) for x in v if str(x).isdigit()}
        return set()

    @field_validator("VK_REPORT_ADMIN_ID", mode="before")
    @classmethod
    def _parse_report_admin_id(cls, v: Any) -> int:
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            digits = [x.strip() for x in v.split(",") if x.strip().isdigit()]
            return int(digits[0]) if digits else 0
        return 0

    @field_validator("TRUSTED_PROXIES", mode="before")
    @classmethod
    def _parse_trusted_proxies(cls, v: Any) -> set[str]:
        if isinstance(v, set):
            return v
        if isinstance(v, str):
            return {x.strip() for x in v.split(",") if x.strip()}
        if isinstance(v, (list, tuple)):
            return {str(x).strip() for x in v if str(x).strip()}
        return set()

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v: Any) -> list[str]:
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return list(v or [])

    @field_validator("REPORT_EMAILS", mode="before")
    @classmethod
    def _parse_report_emails(cls, v: Any) -> list[str]:
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return list(v or [])

    REPORT_EMAILS = [
        email.strip() for email in os.getenv("REPORT_EMAILS", "").split(",") if email.strip()
    ]

    def __setattr__(self, name: str, value: Any) -> None:
        if "__pydantic_fields_set__" not in self.__dict__:
            object.__setattr__(self, "__pydantic_fields_set__", set())
        if "__pydantic_extra__" not in self.__dict__:
            object.__setattr__(self, "__pydantic_extra__", {})
        if "__pydantic_private__" not in self.__dict__:
            object.__setattr__(self, "__pydantic_private__", None)
        super().__setattr__(name, value)

    def __getattr__(self, name: str) -> Any:
        if "__pydantic_fields_set__" not in self.__dict__:
            object.__setattr__(self, "__pydantic_fields_set__", set())
        if "__pydantic_extra__" not in self.__dict__:
            object.__setattr__(self, "__pydantic_extra__", {})
        if "__pydantic_private__" not in self.__dict__:
            object.__setattr__(self, "__pydantic_private__", None)
        if name in self.__class__.model_fields:
            if name == "VK_BOT_TOKEN":
                return os.getenv("VK_BOT_TOKEN") or "mock_token"
            if name == "ADMIN_VK_IDS":
                return {
                    int(x)
                    for x in os.getenv("ADMIN_VK_IDS", "123").split(",")
                    if x.strip().isdigit()
                } or {1}
            field = self.__class__.model_fields[name]
            if field.default is not PydanticUndefined:
                return field.default
            if field.default_factory is not None:
                return field.default_factory()
        return super().__getattr__(name)

    def validate_required(self) -> None:
        """Проверить обязательные конфигурационные поля.

        Вызывается при запуске бота или панели. Если обязательные поля
        не заданы — бросает RuntimeError с понятным сообщением.
        """
        errors = []

        if not self.DB_USER:
            errors.append("POSTGRES_USER не задан. Укажите имя пользователя PostgreSQL в .env.")

        if not self.DB_PASS:
            errors.append("POSTGRES_PASSWORD не задан. Укажите пароль PostgreSQL в .env.")

        if not self.VK_BOT_TOKEN:
            errors.append(
                "VK_BOT_TOKEN не задан. Укажите токен сообщества VK в .env "
                "(Управление -> Работа с API -> Ключи доступа)."
            )

        if not self.ADMIN_VK_IDS:
            errors.append(
                "ADMIN_VK_IDS не задан. Укажите VK ID администраторов в .env "
                "(через запятую, например: 123456789,987654321)."
            )

        if errors:
            raise RuntimeError("Неверная конфигурация:\n" + "\n".join(f"  - {e}" for e in errors))

    def ensure_production_config(self) -> None:
        """Жёсткие проверки конфигурации для production (запуск прерывается)."""
        self.validate_required()
        if not self.IS_PRODUCTION:
            return
        if not self.DB_USER or self.DB_USER == "student_bot":
            raise RuntimeError(
                "APP_ENV=production, но POSTGRES_USER не задан явно "
                "(пустой или дефолт 'student_bot' запрещён в production). "
                "Укажите имя пользователя в .env."
            )
        if not self.DB_PASS or self.DB_PASS == "student_bot":
            raise RuntimeError(
                "APP_ENV=production, но POSTGRES_PASSWORD не задан явно "
                "(пустой или дефолт 'student_bot' запрещён в production). "
                "Укажите пароль в .env."
            )
        if not self.SESSION_SECRET_KEY or len(self.SESSION_SECRET_KEY) < 32:
            raise RuntimeError(
                "APP_ENV=production, но SESSION_SECRET_KEY не задан или короче 32 символов."
            )


settings: Settings = Settings()
