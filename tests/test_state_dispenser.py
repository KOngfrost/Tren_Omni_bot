"""Тесты распределённого State Dispenser VK-бота (Ошибка #15).

Критерий приёмки: сценарий диалога со студентом корректно продолжается
при направлении последовательных запросов на разные воркеры Uvicorn —
FSM-состояние хранится в Redis и доступно любому процессу.
"""

import json

from vkbottle.dispatch.dispenser.base import StatePeer

from bots.vk.common import TicketStates
from core.state_dispenser import RedisStateDispenser


class FakeRedis:
    """Мини-фейк redis.asyncio.Redis: запись/чтение в словарь + опция сбоя."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.fail: bool = False

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        if self.fail:
            raise ConnectionError("redis down")
        self.store[key] = value
        self.ttls[key] = ex

    async def get(self, key: str) -> str | None:
        if self.fail:
            raise ConnectionError("redis down")
        return self.store.get(key)

    async def delete(self, key: str) -> None:
        if self.fail:
            raise ConnectionError("redis down")
        self.store.pop(key, None)
        self.ttls.pop(key, None)


def _patch_redis(monkeypatch, redis: FakeRedis | None):
    """Подменить get_redis_client внутри core.state_dispenser."""

    async def _client():
        return redis

    monkeypatch.setattr("core.state_dispenser.get_redis_client", _client)


PEER = 123456789


async def test_memory_fallback_roundtrip(monkeypatch):
    """Redis не настроен -> in-memory fallback работает как BuiltinStateDispenser."""
    _patch_redis(monkeypatch, None)
    dispenser = RedisStateDispenser()

    assert await dispenser.get(PEER) is None

    await dispenser.set(PEER, TicketStates.WAITING_DESCRIPTION, topic="Жил-быт")
    peer = await dispenser.get(PEER)
    assert isinstance(peer, StatePeer)
    assert peer.payload == {"topic": "Жил-быт"}

    await dispenser.delete(PEER)
    assert await dispenser.get(PEER) is None


async def test_state_equality_survives_roundtrip(monkeypatch):
    """state из Redis корректно сравнивается с enum-состоянием vkbottle.

    StateRule vkbottle сверяет state_peer.state со строкой
    "ClassName:value" — FSM-переходы работают после записи в Redis.
    """
    _patch_redis(monkeypatch, None)
    dispenser = RedisStateDispenser()

    await dispenser.set(PEER, TicketStates.WAITING_IDENTITY_CHOICE, department="Информ")
    peer = await dispenser.get(PEER)

    assert peer is not None
    assert peer.state == TicketStates.WAITING_IDENTITY_CHOICE
    assert peer.state == "TicketStates:waiting_identity_choice"
    assert peer.payload == {"department": "Информ"}


async def test_redis_path_roundtrip_with_ttl(monkeypatch):
    """Состояние пишется в Redis с TTL и читается обратно как StatePeer."""
    fake = FakeRedis()
    _patch_redis(monkeypatch, fake)
    dispenser = RedisStateDispenser()

    await dispenser.set(
        PEER,
        TicketStates.WAITING_DESCRIPTION,
        topic="Культмасс",
        department="Культмасс",
    )

    key = f"bot:state:{PEER}"
    assert key in fake.store
    assert fake.ttls[key] == 3600  # settings.BOT_STATE_TTL_SECONDS по умолчанию

    stored = json.loads(fake.store[key])
    assert stored["peer_id"] == PEER
    assert stored["state"] == "TicketStates:waiting_description"
    assert stored["payload"] == {"topic": "Культмасс", "department": "Культмасс"}

    peer = await dispenser.get(PEER)
    assert peer is not None
    assert peer.state == TicketStates.WAITING_DESCRIPTION
    assert peer.payload == {"topic": "Культмасс", "department": "Культмасс"}

    await dispenser.delete(PEER)
    assert key not in fake.store
    assert await dispenser.get(PEER) is None


async def test_ttl_from_settings_and_custom_ttl(monkeypatch):
    """TTL берётся из settings.BOT_STATE_TTL_SECONDS или явного аргумента."""
    fake = FakeRedis()
    _patch_redis(monkeypatch, fake)
    from core.config import settings

    monkeypatch.setattr(settings, "BOT_STATE_TTL_SECONDS", 777)
    dispenser = RedisStateDispenser()
    await dispenser.set(1, TicketStates.WAITING_DESCRIPTION)
    assert fake.ttls["bot:state:1"] == 777

    custom = RedisStateDispenser(ttl_seconds=60)
    await custom.set(2, TicketStates.WAITING_DESCRIPTION)
    assert fake.ttls["bot:state:2"] == 60


async def test_redis_failure_falls_back_to_memory(monkeypatch):
    """Redis недоступен -> set/get/delete прозрачно работают через память."""
    fake = FakeRedis()
    fake.fail = True
    _patch_redis(monkeypatch, fake)
    dispenser = RedisStateDispenser()

    await dispenser.set(PEER, TicketStates.WAITING_DESCRIPTION, topic="Тема")
    peer = await dispenser.get(PEER)
    assert peer is not None
    assert peer.payload == {"topic": "Тема"}

    await dispenser.delete(PEER)
    assert await dispenser.get(PEER) is None


async def test_corrupt_redis_record_returns_none(monkeypatch):
    """Повреждённая запись в Redis не ломает бота: get -> None (fail-safe)."""
    fake = FakeRedis()
    fake.store[f"bot:state:{PEER}"] = "not-a-json{{{"
    _patch_redis(monkeypatch, fake)
    dispenser = RedisStateDispenser()

    assert await dispenser.get(PEER) is None
    # Повреждённая запись удалена
    assert f"bot:state:{PEER}" not in fake.store


async def test_vk_bot_uses_redis_state_dispenser():
    """Ошибка #15: бот подключён к распределённому диспенсеру состояний."""
    from vkbottle.dispatch.dispenser.abc import ABCStateDispenser

    from bots.vk.bot import vk_bot

    assert isinstance(vk_bot.state_dispenser, RedisStateDispenser)
    assert isinstance(vk_bot.state_dispenser, ABCStateDispenser)


async def test_fsm_dialog_continues_across_dispenser_instances(monkeypatch):
    """Критерий приёмки: диалог продолжается на «другом воркере».

    Шаг 1 выполняет первый экземпляр диспенсера (воркер A, общий Redis),
    шаг 2 — второй экземпляр (воркер B). Состояние и payload доступны
    обоим процессам через Redis.
    """
    fake = FakeRedis()
    _patch_redis(monkeypatch, fake)

    worker_a = RedisStateDispenser()
    # Шаг 1: студент выбрал отдел, бот ждёт описание заявки
    await worker_a.set(
        PEER,
        TicketStates.WAITING_DESCRIPTION,
        topic="Жил-быт",
        department="Жил-быт",
    )

    # Шаг 2: событие попадает на другой воркер — состояние на месте
    worker_b = RedisStateDispenser()
    peer = await worker_b.get(PEER)
    assert peer is not None
    assert peer.state == TicketStates.WAITING_DESCRIPTION
    assert peer.payload == {"topic": "Жил-быт", "department": "Жил-быт"}

    # Завершение диалога на воркере B также видно воркеру A
    await worker_b.delete(PEER)
    assert await worker_a.get(PEER) is None


async def test_set_replaces_payload_atomically(monkeypatch):
    """Повторный set (переход FSM) перезаписывает payload целиком."""
    _patch_redis(monkeypatch, None)
    dispenser = RedisStateDispenser()

    await dispenser.set(PEER, TicketStates.WAITING_DESCRIPTION, topic="Вопрос")
    await dispenser.set(
        PEER,
        TicketStates.WAITING_IDENTITY_CHOICE,
        topic="Вопрос",
        description="Описание обращения студента",
        department=None,
    )
    peer = await dispenser.get(PEER)
    assert peer is not None
    assert peer.state == TicketStates.WAITING_IDENTITY_CHOICE
    assert peer.payload == {
        "topic": "Вопрос",
        "description": "Описание обращения студента",
        "department": None,
    }
