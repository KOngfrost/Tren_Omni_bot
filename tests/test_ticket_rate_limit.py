"""Тесты SEC-08: Redis-based ограничение частоты подачи обращений в VK-боте.

Не более 3 обращений за 5 минут от одного VK ID; при недоступном Redis
ограничение не применяется (fail-open).
"""

from bots.vk.handlers.student import (
    TICKET_RATE_LIMIT,
    TICKET_RATE_WINDOW_SECONDS,
    _is_ticket_rate_limited,
)


class FakeRedis:
    """Мини-фейк redis.asyncio.Redis: INCR со счётчиком и записью TTL."""

    def __init__(self):
        self.store: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True


async def test_rate_limit_allows_first_three_tickets(monkeypatch):
    """Первые 3 обращения проходят, 4-е блокируется (SEC-08)."""
    fake = FakeRedis()

    async def _client():
        return fake

    monkeypatch.setattr("bots.vk.handlers.student.get_redis_client", _client)

    vk_id = 123456
    assert await _is_ticket_rate_limited(vk_id) is False
    assert await _is_ticket_rate_limited(vk_id) is False
    assert await _is_ticket_rate_limited(vk_id) is False
    assert await _is_ticket_rate_limited(vk_id) is True


async def test_rate_limit_sets_window_ttl(monkeypatch):
    """При первом обращении счётчику устанавливается TTL окна (SEC-08)."""
    fake = FakeRedis()

    async def _client():
        return fake

    monkeypatch.setattr("bots.vk.handlers.student.get_redis_client", _client)

    await _is_ticket_rate_limited(42)
    assert fake.ttls.get("ticket_rate:42") == TICKET_RATE_WINDOW_SECONDS


async def test_rate_limit_isolated_per_vk_id(monkeypatch):
    """Лимит считается отдельно для каждого VK ID."""
    fake = FakeRedis()

    async def _client():
        return fake

    monkeypatch.setattr("bots.vk.handlers.student.get_redis_client", _client)

    for _ in range(TICKET_RATE_LIMIT):
        assert await _is_ticket_rate_limited(1) is False
    # Первый пользователь исчерпал лимит, второй — ещё нет
    assert await _is_ticket_rate_limited(1) is True
    assert await _is_ticket_rate_limited(2) is False


async def test_rate_limit_fail_open_without_redis(monkeypatch):
    """Redis недоступен — обращения не блокируются (fail-open)."""

    async def _client():
        return None

    monkeypatch.setattr("bots.vk.handlers.student.get_redis_client", _client)

    for _ in range(TICKET_RATE_LIMIT + 5):
        assert await _is_ticket_rate_limited(999) is False


async def test_rate_limit_fail_open_on_redis_error(monkeypatch):
    """Ошибка Redis при INCR не блокирует обращение (fail-open)."""

    class BrokenRedis:
        async def incr(self, key):
            raise ConnectionError("redis down")

        async def expire(self, key, seconds):
            raise ConnectionError("redis down")

    async def _client():
        return BrokenRedis()

    monkeypatch.setattr("bots.vk.handlers.student.get_redis_client", _client)

    assert await _is_ticket_rate_limited(777) is False
