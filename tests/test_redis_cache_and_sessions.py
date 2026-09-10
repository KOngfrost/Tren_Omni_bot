"""Тестирование распределённого кэша с TTL и сессионного хранилища Redis."""

import pytest

from core.cache import (
    cache_delete,
    cache_delete_pattern,
    cache_get,
    cache_set,
)
from core.redis_client import is_redis_available
from web.security.session_store import RedisSessionMiddleware


@pytest.mark.asyncio
async def test_cache_set_and_get_with_ttl():
    """Проверка записи и чтения кэша с TTL."""
    test_key = "test_key_123"
    test_val = {"foo": "bar", "number": 42}

    # Запись с TTL 2 секунды
    success = await cache_set(test_key, test_val, ttl=2)
    assert success is True

    # Чтение сразу же
    val = await cache_get(test_key)
    assert val == test_val

    # Очистка
    await cache_delete(test_key)
    assert await cache_get(test_key) is None


@pytest.mark.asyncio
async def test_cache_delete_pattern():
    """Проверка очистки кэша по маске."""
    await cache_set("pattern_test:item1", 100, ttl=60)
    await cache_set("pattern_test:item2", 200, ttl=60)
    await cache_set("other_prefix:item3", 300, ttl=60)

    assert await cache_get("pattern_test:item1") == 100
    assert await cache_get("pattern_test:item2") == 200
    assert await cache_get("other_prefix:item3") == 300

    deleted_count = await cache_delete_pattern("pattern_test:*")
    assert deleted_count >= 2

    assert await cache_get("pattern_test:item1") is None
    assert await cache_get("pattern_test:item2") is None
    assert await cache_get("other_prefix:item3") == 300

    # Cleanup
    await cache_delete("other_prefix:item3")


@pytest.mark.asyncio
async def test_redis_availability_check():
    """Проверка функции проверки доступности Redis."""
    available = await is_redis_available()
    assert isinstance(available, bool)


def test_session_middleware_initialization():
    """Проверка инициализации RedisSessionMiddleware."""

    async def dummy_app(scope, receive, send):
        pass

    middleware = RedisSessionMiddleware(
        app=dummy_app,
        secret_key="secret_test_key_12345",
        max_age=3600,
    )
    assert middleware.max_age == 3600
    assert middleware.session_cookie == "session"
