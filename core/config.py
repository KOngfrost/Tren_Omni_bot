import contextlib
import os
import secrets
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

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


class Settings:
    PROJECT_VERSION: str = _project_version

    # === Окружение ===
    # Значения: development | production (плюс test/testing для конфигов).
    # В production: запрещены дефолтные креды БД, пустой SESSION_SECRET_KEY и т.п.
    APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
    dev_environments = {"", "development", "dev", "test", "testing", "local"}

    @property
    def IS_PRODUCTION(self) -> bool:
        return self.APP_ENV not in self.dev_environments

    # Database
    # В production дефолтные значения запрещены — ensure_production_config()
    # поднимет ошибку, если они не переопределены явно в .env.
    DB_USER = os.getenv("POSTGRES_USER", os.getenv("DB_USER", ""))
    DB_PASS = os.getenv("POSTGRES_PASSWORD", os.getenv("DB_PASS", ""))
    DB_NAME = os.getenv("POSTGRES_DB", os.getenv("DB_NAME", "oss_bot"))
    # По умолчанию: localhost для dev, "db" для Docker
    _default_host = "db" if APP_ENV not in dev_environments else "localhost"
    DB_HOST = os.getenv("DB_HOST", _default_host)
    DB_PORT = os.getenv("DB_PORT", "5432")

    # Настройки пула соединений (используется в core/database.py)
    DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "20"))
    DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    DB_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
    DB_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800"))
    DB_POOL_PRE_PING = os.getenv("DB_POOL_PRE_PING", "true").lower() in ("1", "true", "yes")

    # Собираем URL только если все обязательные поля заданы (dev-режим)
    _db_user = quote_plus(DB_USER) if DB_USER else ""
    _db_pass = quote_plus(DB_PASS) if DB_PASS else ""
    database_url = (
        (f"postgresql+asyncpg://{_db_user}:{_db_pass}@{DB_HOST}:{DB_PORT}/{DB_NAME}")
        if DB_USER and DB_PASS
        else ""
    )

    # VK
    VK_BOT_TOKEN = os.getenv("VK_BOT_TOKEN")
    ADMIN_VK_IDS = {
        int(value.strip())
        for value in os.getenv("ADMIN_VK_IDS", "").split(",")
        if value.strip().isdigit()
    }
    VK_REPORT_ADMIN_ID = next(
        (
            int(value.strip())
            for value in os.getenv("VK_REPORT_ADMIN_ID", "0").split(",")
            if value.strip().isdigit()
        ),
        0,
    )

    REPORT_TIME = os.getenv("REPORT_TIME", "09:00")

    # Часовой пояс для отчётов и отображения времени пользователям.
    # Даты в БД хранятся в UTC, отчётные границы считаются в этом поясе.
    APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Europe/Moscow")

    # Dev-режим: разрешить приложению самому создавать базу
    # (CREATE DATABASE). В production должно быть false — базу создаёт
    # PostgreSQL-контейнер через POSTGRES_DB, схема применяется через Alembic.
    ALLOW_DB_CREATE = os.getenv("ALLOW_DB_CREATE", "false").lower() in ("1", "true", "yes")

    # === Безопасность ===

    # Секрет сессий веб-панели.
    # В production ОБЯЗАТЕЛЕН (валидируется в ensure_production_config).
    # В dev-режиме генерируется случайное значение при старте,
    # чтобы сессии не ломались при перезапуске.
    SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "").strip()

    @property
    def session_secret_key(self) -> str:
        """Секрет сессий веб-панели.

        В production обязателен SESSION_SECRET_KEY из .env (проверяется в
        ensure_production_config). В dev при пустом значении используется
        персистентный фоллбэк: файл .session_secret
        (см. _load_or_create_dev_secret).
        """
        if self.SESSION_SECRET_KEY:
            return self.SESSION_SECRET_KEY
        return _load_or_create_dev_secret()

    # === Web admin panel ===
    # https_only для session-cookie:
    #   true (по умолчанию в production) — панель за Nginx/Caddy с TLS или
    #     через Tailscale HTTPS (tailscale serve): кука помечается Secure
    #     и шлётся только по HTTPS.
    #   false (по умолчанию в development) — доступ по http://localhost или
    #     http://tailscale-IP (SSH-туннель / Tailscale без HTTPS).
    #     Кука работает по HTTP (только для разработки).
    _is_prod = APP_ENV not in dev_environments
    _session_https_default = "true" if _is_prod else "false"
    SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", _session_https_default).lower() in (
        "1",
        "true",
        "yes",
    )

    # Доверенные reverse-proxy (IP через запятую), от которых разрешено
    # принимать настоящий IP клиента из заголовка X-Forwarded-For.
    # Пусто — заголовок игнорируется (используется прямой IP соединения).
    TRUSTED_PROXIES = {
        ip.strip() for ip in os.getenv("TRUSTED_PROXIES", "").split(",") if ip.strip()
    }

    # Веб-админка: учётные данные входа (без дефолтов —
    # вход невозможен, пока они не заданы в .env)
    WEB_ADMIN_USERNAME = os.getenv("WEB_ADMIN_USERNAME", "")
    WEB_ADMIN_PASSWORD = os.getenv("WEB_ADMIN_PASSWORD", "")

    # CORS: допустимые источники для веб-панели.
    # По умолчанию — localhost:8000 (dev) и пустой список (production).
    CORS_ORIGINS = [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:8000").split(",")
        if origin.strip()
    ]

    # Фоновый outbox-воркер веб-панели (доставка VK-уведомлений из очереди).
    # При WEB_WORKERS>1 фактическим исполнителем становится ровно один
    # процесс — PostgreSQL advisory lock (core/task_dispatcher.py).
    # false — если доставку должна выполнять только процесс бота.
    WEB_OUTBOX_WORKER = os.getenv("WEB_OUTBOX_WORKER", "true").lower() in (
        "1",
        "true",
        "yes",
    )

    # SMTP (email-рассылка отчётов)
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", ""))
    REPORT_EMAILS = [
        email.strip() for email in os.getenv("REPORT_EMAILS", "").split(",") if email.strip()
    ]

    # Команды VK-бота вынесены в core/commands.py — единый источник правды,
    # который импортируют хендлеры (bots/vk/bot.py).

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
