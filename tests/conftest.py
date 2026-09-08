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
import core.outbox as outbox_module
import core.reporting as reporting_module
import core.ticket_service as ticket_service_module
from core.models import Base


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
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    for module in (database_module, ticket_service_module, reporting_module, outbox_module):
        monkeypatch.setattr(module, "async_session_maker", maker, raising=True)

    yield maker

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

    db_name = f"memdb_{uuid4().hex}"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///file:{db_name}?mode=memory&cache=shared&uri=true"
    )

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    modules_to_patch = [database_module, ticket_service_module, reporting_module, outbox_module]
    for mod_name in (
        "web.routes.tickets", "web.routes.dashboard", "web.routes.faq",
        "web.routes.events", "web.routes.knowledge_base", "web.routes.departments",
        "web.routes.api", "web.routes.logs", "web.routes.admin_panel",
    ):
        mod = __import__(mod_name, fromlist=["async_session_maker"])
        if hasattr(mod, "async_session_maker"):
            modules_to_patch.append(mod)

    for mod in modules_to_patch:
        monkeypatch.setattr(mod, "async_session_maker", maker, raising=True)

    monkeypatch.setattr(database_module, "engine", engine)

    from web.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        client._test_db_name = db_name
        yield client

    asyncio.run(engine.dispose())


