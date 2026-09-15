"""Распределённый State Dispenser для VK-бота (Ошибка #15).

Проблема: штатный BuiltinStateDispenser vkbottle хранит FSM-состояния в
памяти процесса. При горизонтальном масштабировании веб-панели
(WEB_WORKERS > 1, Dockerfile.web) в режиме VK_MODE=callback события
message_new попадают на разные воркеры Uvicorn, и каждый воркер видел
«свою» копию состояния — диалог создания заявки у студента терял шаги.

Решение: кастомный адаптер RedisStateDispenser, реализующий
ABCStateDispenser vkbottle и хранящий состояние и payload пользователя в
Redis (ключ ``bot:state:<peer_id>``, JSON, TTL). Состояние общее для всех
процессов, поэтому любой воркер продолжает диалог студента с того же шага.
TTL продлевается при каждом set() — активный диалог не «умирает» посреди
работы, а брошенные состояния автоматически исчезают из Redis.

Отказоустойчивость: если Redis недоступен (нет REDIS_URL / сеть упала),
диспенсер прозрачно откатывается на in-memory хранилище процесса — так же,
как core/cache.py. Локальная разработка и тесты (REDIS_URL не задан)
работают как со штатным BuiltinStateDispenser.
"""

import contextlib
import json
import logging
from typing import Any

from vkbottle.dispatch.dispenser.abc import ABCStateDispenser
from vkbottle.dispatch.dispenser.base import BaseStateGroup, StatePeer, StateRepresentation

from core.config import settings
from core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

# Отдельный namespace ключей, чтобы не конфликтовать с кэшем (cache:) и
# распределёнными блокировками (distlock:).
_KEY_PREFIX = "bot:state:"


def _key(peer_id: int) -> str:
    return f"{_KEY_PREFIX}{peer_id}"


def _serialize(peer_id: int, state: BaseStateGroup, payload: dict[str, Any]) -> str:
    """Состояние + payload -> JSON-строка для Redis."""
    return json.dumps(
        {
            "peer_id": peer_id,
            # StateRepresentation(state) -> "ClassName:value" (repr, который
            # vkbottle использует при сравнении состояний в StateRule).
            "state": StateRepresentation(state),
            "payload": payload,
        },
        ensure_ascii=False,
        default=str,
    )


def _deserialize(raw: str) -> StatePeer:
    """JSON-строка из Redis -> StatePeer.

    state восстанавливается как StateRepresentation: правило StateRule
    vkbottle сравнивает state_peer.state со строкой "ClassName:value",
    поэтому FSM-переходы (state=TicketStates.WAITING_DESCRIPTION и т.п.)
    работают после любого числа кругов Redis <-> память.
    """
    data = json.loads(raw)
    return StatePeer(
        peer_id=int(data["peer_id"]),
        state=StateRepresentation(data["state"]),
        payload=dict(data.get("payload") or {}),
    )


class RedisStateDispenser(ABCStateDispenser):
    """State Dispenser на Redis с in-memory fallback (см. докстринг модуля)."""

    def __init__(self, ttl_seconds: int | None = None) -> None:
        # None -> TTL берётся из settings.BOT_STATE_TTL_SECONDS при каждом
        # вызове (удобно для тестов через monkeypatch).
        self.default_ttl = ttl_seconds
        # Fallback-хранилище на случай недоступности Redis.
        self._memory: dict[int, StatePeer] = {}

    def _ttl(self) -> int:
        if self.default_ttl is not None:
            return self.default_ttl
        return settings.BOT_STATE_TTL_SECONDS

    async def get(self, peer_id: int) -> StatePeer | None:
        redis = await get_redis_client()
        if redis is not None:
            try:
                raw = await redis.get(_key(peer_id))
            except Exception as exc:
                logger.debug(
                    "StateDispenser: Redis get failed (%s), fallback to memory: %s",
                    _key(peer_id),
                    exc,
                )
                raw = None
            if raw is not None:
                try:
                    return _deserialize(raw)
                except Exception:
                    # Повреждённая запись: сбрасываем состояние пользователя,
                    # чтобы он мог начать диалог заново (fail-safe).
                    logger.exception(
                        "StateDispenser: повреждённая запись состояния peer_id=%s", peer_id
                    )
                    with contextlib.suppress(Exception):
                        await redis.delete(_key(peer_id))
                    return None
        # Записи в Redis нет (или Redis недоступен) — fallback-память процесса.
        return self._memory.get(peer_id)

    async def set(self, peer_id: int, state: BaseStateGroup, **payload: Any) -> None:
        raw = _serialize(peer_id, state, payload)
        redis = await get_redis_client()
        if redis is not None:
            try:
                # ex=... — скользящее окно: TTL продлевается на каждом шаге
                # диалога (set вызывается при каждом переходе FSM).
                await redis.set(_key(peer_id), raw, ex=self._ttl())
                return
            except Exception as exc:
                logger.debug(
                    "StateDispenser: Redis set failed (%s), fallback to memory: %s",
                    _key(peer_id),
                    exc,
                )
        self._memory[peer_id] = StatePeer(peer_id=peer_id, state=state, payload=dict(payload))

    async def delete(self, peer_id: int) -> None:
        redis = await get_redis_client()
        if redis is not None:
            try:
                await redis.delete(_key(peer_id))
            except Exception as exc:
                logger.debug("StateDispenser: Redis delete failed (%s): %s", _key(peer_id), exc)
        self._memory.pop(peer_id, None)


__all__ = ["RedisStateDispenser"]
