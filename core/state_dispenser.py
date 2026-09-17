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

Защита от утечки RAM в fallback-режиме: у каждой записи in-memory словаря
есть TTL (скользящее окно, как в Redis), просроченные состояния вычищаются
периодически (не чаще MEMORY_CLEANUP_INTERVAL_SECONDS) и немедленно при
переполнении MEMORY_MAX_ENTRIES. Фоновый периодический сборщик запускается
через start_maintenance()/stop_maintenance().
"""

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

from vkbottle.dispatch.dispenser.abc import ABCStateDispenser
from vkbottle.dispatch.dispenser.base import BaseStateGroup, StatePeer, StateRepresentation

from core.config import settings
from core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

# Отдельный namespace ключей, чтобы не конфликтовать с кэшем (cache:) и
# распределёнными блокировками (distlock:).
_KEY_PREFIX = "bot:state:"

# In-memory fallback живёт столько же, сколько запись в Redis (TTL из
# settings.BOT_STATE_TTL_SECONDS), но Redis чистит ключи сам, а словарь
# процесса — нет. Период полной ревизии словаря (O(N)): не чаще раза в
# минуту, чтобы не замедлять горячий путь get/set на длинных диалогах.
MEMORY_CLEANUP_INTERVAL_SECONDS = 60.0
# Жёсткий потолок числа состояний в памяти одного процесса. При превышении
# вытесняются записи с ближайшим сроком истечения — память не растёт
# бесконечно даже при длительном простое Redis.
MEMORY_MAX_ENTRIES = 1000


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

    def __init__(
        self,
        ttl_seconds: int | None = None,
        *,
        memory_max_entries: int = MEMORY_MAX_ENTRIES,
        memory_cleanup_interval: float = MEMORY_CLEANUP_INTERVAL_SECONDS,
    ) -> None:
        # None -> TTL берётся из settings.BOT_STATE_TTL_SECONDS при каждом
        # вызове (удобно для тестов через monkeypatch).
        self.default_ttl = ttl_seconds
        # Fallback-хранилище на случай недоступности Redis:
        # peer_id -> (срок_истечения_monotonic, StatePeer).
        self._memory: dict[int, tuple[float, StatePeer]] = {}
        self._memory_max_entries = memory_max_entries
        self._memory_cleanup_interval = memory_cleanup_interval
        self._last_memory_cleanup = time.monotonic()
        self._maintenance_task: asyncio.Task[None] | None = None

    def _ttl(self) -> int:
        if self.default_ttl is not None:
            return self.default_ttl
        return settings.BOT_STATE_TTL_SECONDS

    # --- In-memory fallback: TTL + периодическая очистка -------------------

    def cleanup_expired_states(self, now: float | None = None) -> int:
        """Удалить просроченные FSM-состояния из fallback-памяти.

        Возвращает число удалённых записей. Если словарь больше
        ``memory_max_entries``, дополнительно вытесняются самые «протухающие»
        записи — так словарь не растёт бесконечно при недоступном Redis.
        """
        now = time.monotonic() if now is None else now
        self._last_memory_cleanup = now

        removed = [peer_id for peer_id, (deadline, _) in self._memory.items() if deadline <= now]
        for peer_id in removed:
            self._memory.pop(peer_id, None)

        overflow = len(self._memory) - self._memory_max_entries
        if overflow > 0:
            stale = sorted(self._memory.items(), key=lambda item: item[1][0])[:overflow]
            for peer_id, _ in stale:
                self._memory.pop(peer_id, None)
            removed.extend(peer_id for peer_id, _ in stale)

        if removed:
            logger.debug(
                "StateDispenser: очищено fallback-состояний: %s, осталось: %s",
                len(removed),
                len(self._memory),
            )
        return len(removed)

    def _memory_get(self, peer_id: int) -> StatePeer | None:
        """Чтение из fallback-памяти с проверкой TTL."""
        entry = self._memory.get(peer_id)
        if entry is None:
            return None
        deadline, peer = entry
        if deadline <= time.monotonic():
            self._memory.pop(peer_id, None)
            return None
        return peer

    def _memory_set(self, peer_id: int, state: BaseStateGroup, payload: dict[str, Any]) -> None:
        """Запись в fallback-память: TTL продлевается на каждом шаге диалога."""
        self._memory[peer_id] = (
            time.monotonic() + self._ttl(),
            StatePeer(peer_id=peer_id, state=state, payload=dict(payload)),
        )
        # Периодическая ревизия по времени либо немедленная при переполнении.
        now = time.monotonic()
        if (
            now - self._last_memory_cleanup >= self._memory_cleanup_interval
            or len(self._memory) > self._memory_max_entries
        ):
            self.cleanup_expired_states(now)

    async def _maintenance_loop(self) -> None:
        """Фоновый периодический сборщик устаревших состояний fallback-памяти."""
        while True:
            try:
                await asyncio.sleep(self._memory_cleanup_interval)
                self.cleanup_expired_states()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("StateDispenser: ошибка фоновой очистки состояний")

    def start_maintenance(self) -> asyncio.Task[None]:
        """Запустить периодическую очистку fallback-памяти (идемпотентно).

        Вызывается из lifespan веб-панели (VK_MODE=callback) и из планировщика
        бота: без него TTL всё равно работает (проверка при чтении и
        периодическая ревизия при записи), фоновая задача лишь гарантирует
        освобождение RAM при простое без входящих сообщений.
        """
        if self._maintenance_task is not None and not self._maintenance_task.done():
            return self._maintenance_task
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())
        return self._maintenance_task

    async def stop_maintenance(self) -> None:
        """Остановить фоновый сборщик (graceful shutdown)."""
        task, self._maintenance_task = self._maintenance_task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # --- ABCStateDispenser --------------------------------------------------

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
        return self._memory_get(peer_id)

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
        self._memory_set(peer_id, state, payload)

    async def delete(self, peer_id: int) -> None:
        redis = await get_redis_client()
        if redis is not None:
            try:
                await redis.delete(_key(peer_id))
            except Exception as exc:
                logger.debug("StateDispenser: Redis delete failed (%s): %s", _key(peer_id), exc)
        self._memory.pop(peer_id, None)


__all__ = ["RedisStateDispenser"]
