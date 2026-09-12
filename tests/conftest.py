"""Общие фикстуры тестов: in-memory SQLite вместо PostgreSQL."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Секрет обязателен для импорта web.main
os.environ.setdefault("SESSION_SECRET_KEY", "test_secret_key_for_tests_1234567890")
# Тесты не должны зависеть от production-настроек локального .env.
os.environ["APP_ENV"] = "development"
os.environ["SESSION_HTTPS_ONLY"] = "false"

import asyncio

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import core.database as database_module
from core.models import Base

ALL_MODULE_NAMES = (
    "core.database",
    "core.ticket_service",
    "core.reporting",
    "core.outbox",
    "core.bot_core",
    "bots.vk.bot",
    "bots.vk.common",
    "bots.vk.handlers.admin",
    "bots.vk.handlers.events",
    "bots.vk.handlers.faq",
    "bots.vk.handlers.knowledge",
    "bots.vk.handlers.reports",
    "bots.vk.handlers.student",
    "web.main",
    "web.dependencies",
    "web.routes.tickets",
    "web.routes.dashboard",
    "web.routes.faq",
    "web.routes.events",
    "web.routes.knowledge_base",
    "web.routes.departments",
    "web.routes.api",
    "web.routes.api_v1",
    "web.routes.logs",
    "web.routes.admin_panel",
    "web.routes.auth",
    "web.routes.dept_frame",
)


def _patch_all_modules(monkeypatch, maker, engine):
    monkeypatch.setattr(database_module, "engine", engine)
    for mod_name in ALL_MODULE_NAMES:
        try:
            mod = __import__(mod_name, fromlist=["async_session_maker"])
            if hasattr(mod, "async_session_maker"):
                monkeypatch.setattr(mod, "async_session_maker", maker, raising=False)
        except Exception:
            pass


@pytest_asyncio.fixture
async def db_session_maker(monkeypatch):
    """Async session maker на in-memory SQLite с подменой во всех модулях.

    Подмена выполняется через monkeypatch: pytest гарантированно
    восстанавливает исходные атрибуты модулей даже при падении теста.
    ВАЖНО: паттерн с подменой атрибутов модулей несовместим с запуском
    тестов в нескольких потоках одного процесса; при распараллеливании
    используйте отдельные процессы (pytest-xdist) либо рефакторинг
    сервисов на явную передачу session-factory.
    """
    from sqlalchemy import event

    from core.config import settings

    if not os.getenv("TEST_REDIS_URL"):
        monkeypatch.setattr(settings, "REDIS_URL", None)
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", False)

    test_pg_url = os.getenv("TEST_POSTGRES_URL")
    if test_pg_url:
        engine = create_async_engine(test_pg_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    else:
        engine = create_async_engine("sqlite+aiosqlite://")

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_custom_lower(dbapi_con, con_record):
            if hasattr(dbapi_con, "create_function"):
                dbapi_con.create_function(
                    "lower", 1, lambda s: s.lower() if s is not None else None
                )

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    _patch_all_modules(monkeypatch, maker, engine)

    try:
        yield maker
    finally:
        if test_pg_url:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.fixture
def web_client(monkeypatch):
    """TestClient с in-memory SQLite (shared cache) и подменой БД во всех модулях.

    Особенности:
    - База: sqlite file:memdb_<uuid>?mode=memory&cache=shared — общая для
       aiosqlite (через приложение) и для stdlib sqlite3 (для прямых
       проверок данных из синхронных тестов через client._test_db_name).
    - Подменяются async_session_maker во всех модулях приложения
       (включая веб-роуты) — pytest восстанавливает атрибуты после теста.
    - Bootstrap-креды патчатся на Singleton settings (не env vars).
    """
    from uuid import uuid4

    from core.config import settings

    # Bootstrap-креды патчим у Singleton-объекта settings (не env vars),
    # т.к. Settings уже был импортирован на момент создания фикстуры.
    monkeypatch.setattr(settings, "WEB_ADMIN_USERNAME", "testadmin")
    monkeypatch.setattr(settings, "WEB_ADMIN_PASSWORD", "test_password_123")
    monkeypatch.setattr(settings, "VK_BOT_TOKEN", "test_vk_token")
    monkeypatch.setattr(settings, "ADMIN_VK_IDS", {123456789})
    monkeypatch.setattr(settings, "VK_REPORT_ADMIN_ID", 123456789)
    # Отключаем внешние сетевые зависимости для быстрых локальных тестов
    if not os.getenv("TEST_REDIS_URL"):
        monkeypatch.setattr(settings, "REDIS_URL", None)
    monkeypatch.setattr(settings, "TWO_FACTOR_ENABLED", False)

    db_name = f"memdb_{uuid4().hex}"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///file:{db_name}?mode=memory&cache=shared&uri=true"
    )

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    _patch_all_modules(monkeypatch, maker, engine)

    from web.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        client._test_db_name = db_name
        yield client

    asyncio.run(engine.dispose())
