import re

import pytest

from bots.vk.keyboards import (
    _chunk_buttons,
)
from core.bot_core import BotCore, get_admin_scope_for_vk_id
from core.commands import (
    ADMIN_REPLY_PATTERN,
    COMMANDS_REPORT_BY_DATE,
    COMMANDS_REPORT_BY_PERIOD,
    STUDENT_REPLY_PATTERN,
    TICKET_REPLY_PATTERN,
)
from core.heartbeat import heartbeat_age_seconds, is_healthy, touch_heartbeat
from core.models import Admin, UserRole


def test_chunk_buttons():
    items = [1, 2, 3, 4, 5]
    chunks = _chunk_buttons(items, 2)
    assert chunks == [[1, 2], [3, 4], [5]]


def test_reply_patterns_unification():
    assert ADMIN_REPLY_PATTERN == TICKET_REPLY_PATTERN
    assert STUDENT_REPLY_PATTERN == TICKET_REPLY_PATTERN

    match1 = re.match(TICKET_REPLY_PATTERN, "Ответ #123: Здравствуйте")
    assert match1 is not None
    assert match1.group(1) == "123"
    assert match1.group(2) == "Здравствуйте"

    match2 = re.match(TICKET_REPLY_PATTERN, "Ответ 456 Спасибо за помощь")
    assert match2 is not None
    assert match2.group(1) == "456"
    assert match2.group(2) == "Спасибо за помощь"


def test_report_commands_content():
    assert "Отчет по дате" in COMMANDS_REPORT_BY_DATE
    assert "отчет по дате" in COMMANDS_REPORT_BY_DATE
    assert "Отчет за период" in COMMANDS_REPORT_BY_PERIOD
    assert "отчет за период" in COMMANDS_REPORT_BY_PERIOD


def test_heartbeat_cycle(tmp_path, monkeypatch):
    test_file = str(tmp_path / "test_heartbeat")
    import core.heartbeat as hb

    monkeypatch.setattr(hb, "HEARTBEAT_FILE", test_file)

    touch_heartbeat()
    age = heartbeat_age_seconds()
    assert age is not None
    assert age < 5.0
    assert is_healthy() is True


@pytest.mark.asyncio
async def test_bot_core_user_and_admin(db_session_maker):
    user = await BotCore.get_or_create_user(vk_id=987654)
    assert user.id is not None
    assert user.vk_id == 987654

    user2 = await BotCore.get_or_create_user(vk_id=987654)
    assert user2.id == user.id

    is_adm = await BotCore.is_admin(user)
    assert is_adm is False

    async with db_session_maker() as session:
        admin = Admin(user_id=user.id, role=UserRole.SUPERADMIN)
        session.add(admin)
        await session.commit()

    is_adm2 = await BotCore.is_admin(user)
    assert is_adm2 is True

    async with db_session_maker() as session:
        is_super, dept_id = await get_admin_scope_for_vk_id(session, 987654)
        assert is_super is True
        assert dept_id is None


@pytest.mark.asyncio
async def test_graceful_shutdown_cleanup():
    """Тест корректного завершения и освобождения ресурсов (Redis, DB, background tasks)."""
    import asyncio
    from unittest.mock import AsyncMock

    import core.redis_client as rc
    from core.database import dispose_engine
    from core.redis_client import close_redis_client

    # Моделируем активный клиент Redis
    mock_redis = AsyncMock()
    mock_redis.close = AsyncMock()
    rc._redis_client = mock_redis

    await close_redis_client()
    assert mock_redis.close.called
    assert rc._redis_client is None

    # Проверяем dispose_engine
    await dispose_engine()

    # Проверяем отмену и сбор фоновых задач
    async def sample_worker():
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(sample_worker())
    assert not task.done()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert task.done()
    assert task.cancelled()

