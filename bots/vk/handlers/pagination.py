"""Пагинация списков VK-бота: состояние страницы и общие кнопки навигации.

ЭТАП 4.1 (B4). Кнопки «Ещё ➡️» / «⬅️ Назад» общие для всех списков
(«Мои заявки», заявки администратора, разделы FAQ), поэтому хендлеры
навигации лежат здесь, в одном месте: vkbottle обрабатывает только первый
совпавший хендлер, и дублирование text-правил в разных модулях привело бы
к тому, что часть кнопок просто не работала.

Текущая страница хранится per-user под ключом ``vk:pagination:{vk_id}``
в Redis (TTL 30 минут, распределён между воркерами при WEB_WORKERS>1).
При недоступности Redis используется in-memory fallback с тем же TTL
(fail-open: пагинация продолжает работать, но может не пережить
перезапуск процесса).

Сами данные списка не кешируются — при каждом перелистывании рендерер
заново читает БД, поэтому состояние не устаревает относительно заявок.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from vkbottle.bot import BotLabeler, Message

from bots.vk.common import _main_keyboard_for
from core.commands import COMMANDS_PAGINATION_NEXT, COMMANDS_PAGINATION_PREV
from core.heartbeat import touch_heartbeat
from core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

# Размер одной страницы: 5 записей + ряд навигации влезают в один экран
PAGE_SIZE = 5
# Сколько записей забираем из БД максимум (ЭТАП 4.1: «все записи 10–25»)
FETCH_LIMIT = 25
# TTL состояния пагинации
STATE_TTL_SECONDS = 1800

# Идентификаторы списков (kind), под которыми хендлеры регистрируют рендереры
KIND_MY_TICKETS = "my_tickets"
KIND_ADMIN_TICKETS = "admin_tickets"
KIND_FAQ_DEPARTMENTS = "faq_departments"
KIND_FAQ_SEARCH = "faq_search"

# Рендерер страницы: (message, page, meta) -> None. Регистрируется
# хендлер-модулем при импорте (см. register_renderer).
Renderer = Callable[[Message, int, dict[str, Any]], Awaitable[None]]
_renderers: dict[str, Renderer] = {}

# In-memory fallback состояния: vk_id -> (expiry_ts, kind, page, meta)
_local_states: dict[int, tuple[float, str, int, dict[str, Any]]] = {}

_STATE_KEY_PREFIX = "vk:pagination:"

pagination_labeler = BotLabeler()


def register_renderer(kind: str, renderer: Renderer) -> None:
    """Зарегистрировать рендерер страниц списка с указанным kind."""
    _renderers[kind] = renderer


def page_count(total: int) -> int:
    """Количество страниц при данном размере записи (минимум 1)."""
    if total <= 0:
        return 1
    return (total + PAGE_SIZE - 1) // PAGE_SIZE


def clamp_page(page: int, total: int) -> int:
    """Ограничить номер страницы диапазоном [0; last_page]."""
    return max(0, min(page, page_count(total) - 1))


def _prune_local_states(now: float | None = None) -> None:
    """Удалить истёкшие in-memory записи (защита от утечки памяти)."""
    now = time.time() if now is None else now
    expired = [uid for uid, (expiry, *_rest) in _local_states.items() if expiry <= now]
    for uid in expired:
        del _local_states[uid]


async def _save_state(vk_id: int, kind: str, page: int, meta: dict[str, Any]) -> None:
    payload = json.dumps({"kind": kind, "page": page, "meta": meta}, ensure_ascii=False)
    redis = await get_redis_client()
    if redis is not None:
        try:
            await redis.setex(f"{_STATE_KEY_PREFIX}{vk_id}", STATE_TTL_SECONDS, payload)
            return
        except Exception as e:
            logger.warning("Пагинация: не удалось сохранить состояние в Redis: %s", e)

    now = time.time()
    _prune_local_states(now)
    _local_states[vk_id] = (now + STATE_TTL_SECONDS, kind, page, meta)


async def _load_state(vk_id: int) -> tuple[str, int, dict[str, Any]] | None:
    """Прочитать текущее состояние (kind, page, meta) или None, если истекло."""
    redis = await get_redis_client()
    if redis is not None:
        try:
            raw = await redis.get(f"{_STATE_KEY_PREFIX}{vk_id}")
            if raw:
                data = json.loads(raw)
                return str(data["kind"]), int(data.get("page", 0)), dict(data.get("meta") or {})
        except Exception as e:
            logger.warning("Пагинация: не удалось прочитать состояние из Redis: %s", e)

    now = time.time()
    _prune_local_states(now)
    entry = _local_states.get(vk_id)
    if entry is None:
        return None
    _expiry, kind, page, meta = entry
    return kind, page, meta


async def persist_page(
    vk_id: int, kind: str, page: int, meta: dict[str, Any] | None = None
) -> None:
    """Сохранить текущую страницу списка (вызывается рендерером после clamp)."""
    await _save_state(vk_id, kind, page, meta or {})


async def show_page(
    message: Message,
    kind: str,
    page: int,
    meta: dict[str, Any] | None = None,
) -> None:
    """Отрисовать страницу списка через зарегистрированный рендерер.

    Рендерер сам ограничивает номер страницы последней допустимой и
    сохраняет его через persist_page (см. clamp_page).
    """
    renderer = _renderers.get(kind)
    if renderer is None:
        logger.error("Пагинация: рендерер для списка %r не зарегистрирован", kind)
        await message.answer(
            "Список временно недоступен. Пожалуйста, вернитесь в меню.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return
    await renderer(message, page, meta or {})


async def open_list(
    message: Message,
    kind: str,
    meta: dict[str, Any] | None = None,
) -> None:
    """Открыть список с первой страницы (сброс состояния пагинации)."""
    await show_page(message, kind, 0, meta)


async def _turn_page(message: Message, delta: int) -> None:
    """Общая обработка кнопок «Ещё ➡️» / «⬅️ Назад»."""
    touch_heartbeat()
    state = await _load_state(message.from_id)
    if state is None:
        await message.answer(
            "Список устарел или был закрыт. Пожалуйста, откройте его заново из меню.",
            keyboard=await _main_keyboard_for(message.from_id),
        )
        return
    kind, page, meta = state
    await show_page(message, kind, max(0, page + delta), meta)


@pagination_labeler.private_message(text=COMMANDS_PAGINATION_NEXT)
async def pagination_next_handler(message: Message):
    """Кнопка «Ещё ➡️»: показать следующую страницу списка."""
    await _turn_page(message, 1)


@pagination_labeler.private_message(text=COMMANDS_PAGINATION_PREV)
async def pagination_prev_handler(message: Message):
    """Кнопка «⬅️ Назад»: показать предыдущую страницу списка."""
    await _turn_page(message, -1)
