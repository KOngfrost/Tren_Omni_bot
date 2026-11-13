"""тесты для модуля управления администраторами telegram core/telegram_admins.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.telegram_admins import (
    _reset_cache,
    add_telegram_admin_id,
    get_authorized_telegram_admin_ids,
    is_authorized_telegram_admin,
    remove_telegram_admin_id,
)


@pytest.fixture(autouse=True)
def reset_in_memory_cache() -> None:
    """сброс кеша перед каждым тестом."""
    _reset_cache()


@pytest.mark.asyncio
async def test_get_authorized_admins_includes_root() -> None:
    """проверка, что список администраторов всегда включает root admin."""
    root_id = 6665679072
    with patch("core.telegram_admins._read_redis_admin_ids", new_callable=AsyncMock) as mock_redis:
        mock_redis.return_value = set()
        admins = await get_authorized_telegram_admin_ids(root_id)
        assert root_id in admins


@pytest.mark.asyncio
async def test_get_authorized_admins_merges_redis() -> None:
    """проверка объединения root admin и id из хранилища."""
    root_id = 6665679072
    extra_id = 7328025089
    with patch("core.telegram_admins._read_redis_admin_ids", new_callable=AsyncMock) as mock_redis:
        mock_redis.return_value = {extra_id}
        admins = await get_authorized_telegram_admin_ids(root_id)
        assert root_id in admins
        assert extra_id in admins


@pytest.mark.asyncio
async def test_add_telegram_admin_success() -> None:
    """проверка успешного добавления нового администратора."""
    root_id = 6665679072
    new_id = 7328025089
    with patch("core.telegram_admins._read_redis_admin_ids", new_callable=AsyncMock) as mock_read, \
         patch("core.telegram_admins._write_redis_admin_ids", new_callable=AsyncMock) as mock_write, \
         patch("core.telegram_admins._write_fallback_admin_ids") as mock_fallback:
        mock_read.return_value = set()
        mock_write.return_value = True

        ok, msg = await add_telegram_admin_id(new_id, added_by=f"telegram:{root_id}")
        assert ok is True
        assert "успешно добавлен" in msg.lower()
        mock_write.assert_awaited_once()
        mock_fallback.assert_called_once()


@pytest.mark.asyncio
async def test_add_telegram_admin_invalid_id() -> None:
    """проверка отклонения некорректного telegram id."""
    ok, msg = await add_telegram_admin_id(-5, added_by="admin")
    assert ok is False
    assert "некорректный" in msg.lower()

    ok2, msg2 = await add_telegram_admin_id(0, added_by="admin")
    assert ok2 is False
    assert "некорректный" in msg2.lower()


@pytest.mark.asyncio
async def test_add_telegram_admin_already_exists() -> None:
    """проверка обработки попытки добавления уже существующего администратора."""
    root_id = 6665679072
    with patch("core.telegram_admins._read_redis_admin_ids", new_callable=AsyncMock) as mock_read:
        mock_read.return_value = set()
        ok, msg = await add_telegram_admin_id(root_id, added_by="admin", root_id=root_id)
        assert ok is False
        assert "уже является" in msg.lower()


@pytest.mark.asyncio
async def test_remove_telegram_admin_success() -> None:
    """проверка успешного удаления администратора."""
    root_id = 6665679072
    target_id = 7328025089
    with patch("core.telegram_admins._read_redis_admin_ids", new_callable=AsyncMock) as mock_read, \
         patch("core.telegram_admins._write_redis_admin_ids", new_callable=AsyncMock) as mock_write, \
         patch("core.telegram_admins._write_fallback_admin_ids") as mock_fallback:
        mock_read.return_value = {target_id}
        mock_write.return_value = True

        ok, msg = await remove_telegram_admin_id(target_id, root_id=root_id, removed_by="admin")
        assert ok is True
        assert "удален" in msg.lower()
        mock_write.assert_awaited_once()
        mock_fallback.assert_called_once()


@pytest.mark.asyncio
async def test_remove_telegram_admin_cannot_remove_root() -> None:
    """проверка строгого запрета на удаление главного администратора."""
    root_id = 6665679072
    ok, msg = await remove_telegram_admin_id(root_id, root_id=root_id, removed_by="admin")
    assert ok is False
    assert "главного администратора" in msg.lower()


@pytest.mark.asyncio
async def test_is_authorized_telegram_admin() -> None:
    """проверка авторизации пользователей (root, дополнительный, посторонний)."""
    root_id = 6665679072
    extra_id = 7328025089
    stranger_id = 999999999

    with patch("core.telegram_admins._read_redis_admin_ids", new_callable=AsyncMock) as mock_read:
        mock_read.return_value = {extra_id}

        # root всегда авторизован
        assert await is_authorized_telegram_admin(root_id, root_id) is True
        # дополнительный авторизован
        assert await is_authorized_telegram_admin(extra_id, root_id) is True
        # посторонний отклонен
        assert await is_authorized_telegram_admin(stranger_id, root_id) is False
