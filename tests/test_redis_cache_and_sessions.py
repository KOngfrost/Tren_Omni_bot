"""Тестирование распределённого кэша с TTL и сессионного хранилища Redis."""

import base64
import contextlib

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


# ---------------------------------------------------------------------------
# SEC-09: шифрование данных fallback-сессии (Fernet) при записи в cookie.
# ---------------------------------------------------------------------------


def _make_middleware() -> RedisSessionMiddleware:
    async def dummy_app(scope, receive, send):
        pass

    return RedisSessionMiddleware(
        app=dummy_app,
        secret_key="secret_test_key_12345",
        max_age=3600,
    )


@pytest.mark.asyncio
async def test_fallback_session_cookie_is_encrypted():
    """Данные fallback-сессии не читаются из cookie как открытый Base64 (SEC-09)."""
    mw = _make_middleware()
    session = {"user": {"id": 42, "username": "admin"}, "department_id": 7}

    cookie_val, session_id = await mw._persist_session(session, None, None)
    assert session_id is None  # fallback-режим: Redis недоступен

    # Подпись itsdangerous: payload до первой точки не должен содержать
    # читаемые данные сессии (раньше там был открытый Base64 JSON).
    payload = cookie_val.split(".")[0]
    with contextlib.suppress(Exception):
        decoded = base64.b64decode(payload + "==").decode("utf-8", errors="replace")
        assert "42" not in decoded
        assert "admin" not in decoded
        assert "department" not in decoded
    # Fernet-токен начинается с версии 0x80 (Base64: "gAAAAA").
    unsigned = mw.signer.unsign(cookie_val.encode()).decode()
    assert unsigned.startswith("gAAAAA")


@pytest.mark.asyncio
async def test_fallback_session_roundtrip():
    """Зашифрованная fallback-сессия корректно восстанавливается (SEC-09)."""
    mw = _make_middleware()
    session = {"user": {"id": 42}, "department_id": 7, "csrf_token": "tok"}

    cookie_val, _ = await mw._persist_session(session, None, None)
    session_id, loaded = await mw._load_session(cookie_val, None)

    assert session_id is None
    assert loaded == session


@pytest.mark.asyncio
async def test_fallback_session_rejects_tampered_cookie():
    """Подделанная/повреждённая cookie приравнивается к отсутствующей сессии."""
    mw = _make_middleware()
    session = {"user": {"id": 42}}
    cookie_val, _ = await mw._persist_session(session, None, None)

    tampered = cookie_val[:-2] + ("aa" if cookie_val[-2:] != "aa" else "bb")
    _, loaded = await mw._load_session(tampered, None)
    assert loaded == {}


@pytest.mark.asyncio
async def test_fallback_session_rejects_legacy_plaintext_cookie():
    """Старая (незашифрованная) cookie-сессия после включения SEC-09 невалидна."""
    mw = _make_middleware()
    legacy_payload = base64.b64encode(b'{"user": {"id": 42}}').decode()
    legacy_cookie = mw.signer.sign(legacy_payload.encode()).decode()

    _, loaded = await mw._load_session(legacy_cookie, None)
    assert loaded == {}
