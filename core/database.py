import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.config import settings

# Пул соединений: AsyncAdaptedQueuePool заменяет NullPool.
# NullPool открывал новое TCP-соединение на каждый запрос — при 2500+
# пользователях это гарантированный bottleneck для PostgreSQL.
#
# Параметры пула читаются из настроек (core/config.py):
#   DB_POOL_SIZE     — базовый размер (по умолчанию 20)
#   DB_MAX_OVERFLOW  — максимальные дополнительные соединения (по умолчанию 10)
#   DB_POOL_TIMEOUT  — таймаут получения соединения (по умолчанию 30 сек)
#   DB_POOL_RECYCLE  — время жизни соединения, сек (по умолчанию 1800 = 30 мин)
#   DB_POOL_PRE_PING — проверка соединения перед выдачей (по умолчанию true)
engine: AsyncEngine | None
if settings.database_url:
    connect_args = {}
    if "asyncpg" in settings.database_url:
        if settings.DB_USE_PGBOUNCER or settings.DB_STATEMENT_CACHE_SIZE == 0:
            connect_args["statement_cache_size"] = 0
            connect_args["prepared_statement_cache_size"] = 0
        elif settings.DB_STATEMENT_CACHE_SIZE:
            connect_args["statement_cache_size"] = settings.DB_STATEMENT_CACHE_SIZE

    engine = create_async_engine(
        settings.database_url,
        echo=False,
        connect_args=connect_args,
        poolclass=pool.AsyncAdaptedQueuePool,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT,
        pool_recycle=settings.DB_POOL_RECYCLE,
        pool_pre_ping=settings.DB_POOL_PRE_PING,
    )
    if "sqlite" in settings.database_url:
        from sqlalchemy import event

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_custom_lower(dbapi_con, con_record):
            if hasattr(dbapi_con, "create_function"):
                dbapi_con.create_function(
                    "lower", 1, lambda s: s.lower() if s is not None else None
                )
else:
    # Учётные данные не заданы (например, в CI): движок не создаём,
    # тесты используют свой in-memory SQLite (см. tests/conftest.py).
    engine = None
# Сессия-мейкер создаётся всегда: в CI/тестах фиксстура подменяет его,
# в приложении креды БД гарантированы (ensure_production_config).
# Пустой engine (нет кредов) приведёт к ошибке только при запросе —
# тип остаётся не-Optional, что честно для 35+ мест использования.
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Строгая валидация имени базы данных.
# - Начинается с буквы или подчёркивания
# - Только буквы, цифры, подчёркивания
# - Максимум 63 символа (лимит PostgreSQL)
# - Не начинается с цифры (невозможно в PostgreSQL anyway)
# - Не содержит пробелов, дефисов, спецсимволов
DATABASE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

# Зарезервированные имена PostgreSQL, которые нельзя использовать
POSTGRES_RESERVED_DB_NAMES = {
    "postgres",
    "template0",
    "template1",
}


async def ensure_database_exists(max_retries: int = 10, retry_delay: float = 2.0) -> None:
    if not DATABASE_NAME_PATTERN.fullmatch(settings.DB_NAME):
        raise ValueError(
            f"DB_NAME содержит недопустимые символы: {settings.DB_NAME!r}. "
            "Разрешены только буквы, цифры и подчёркивания (макс. 63 символа)."
        )

    # Защита от использования зарезервированных имён PostgreSQL
    if settings.DB_NAME.lower() in POSTGRES_RESERVED_DB_NAMES:
        raise ValueError(
            f"DB_NAME '{settings.DB_NAME}' — зарезервированное имя PostgreSQL. "
            "Используйте другое имя базы данных."
        )

    last_error = None

    for attempt in range(1, max_retries + 1):
        admin_conn = None
        try:
            admin_conn = await asyncpg.connect(
                user=settings.DB_USER,
                password=settings.DB_PASS,
                database="postgres",
                host=settings.DB_HOST,
                port=int(settings.DB_PORT),
            )
            db_exists = await admin_conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1",
                settings.DB_NAME,
            )
            if not db_exists:
                await admin_conn.execute(f'CREATE DATABASE "{settings.DB_NAME}"')
            return
        except Exception as exc:
            last_error = exc
            if attempt == max_retries:
                raise
            await asyncio.sleep(retry_delay)
        finally:
            if admin_conn is not None:
                await admin_conn.close()

    if last_error is not None:
        raise last_error


def run_migrations(max_retries: int = 5, retry_delay: float = 2.0) -> None:
    """Apply Alembic migrations to the database."""
    from alembic import command
    from alembic.config import Config

    last_error = None

    alembic_ini_path = str(Path(__file__).resolve().parent.parent / "alembic.ini")
    for attempt in range(1, max_retries + 1):
        try:
            alembic_cfg = Config(alembic_ini_path)
            command.upgrade(alembic_cfg, "head")
            return
        except Exception as exc:
            last_error = exc
            if attempt == max_retries:
                raise
            time.sleep(retry_delay)

    if last_error is not None:
        raise last_error


async def dispose_engine() -> None:
    if engine is not None:
        await engine.dispose()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_maker() as session:
        yield session
