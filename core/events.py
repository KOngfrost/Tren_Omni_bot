import asyncio
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

_ticket_change_listeners: list[Callable[[], None]] = []
_background_tasks: set[asyncio.Task[None]] = set()
REDIS_TICKET_EVENTS_CHANNEL = "channel:ticket_events"


def register_ticket_change_listener(callback: Callable[[], None]) -> None:
    """Зарегистрировать функцию обратного вызова при изменении заявок."""
    if callback not in _ticket_change_listeners:
        _ticket_change_listeners.append(callback)


def unregister_ticket_change_listener(callback: Callable[[], None]) -> None:
    """Удалить слушателя изменений заявок."""
    if callback in _ticket_change_listeners:
        _ticket_change_listeners.remove(callback)


async def _publish_ticket_change_to_redis() -> None:
    """Опубликовать событие изменения в Redis Pub/Sub для мгновенной доставки во все воркеры."""
    try:
        from core.redis_client import get_redis_client

        redis = await get_redis_client()
        if redis is not None:
            await redis.publish(REDIS_TICKET_EVENTS_CHANNEL, "ticket_changed")
    except Exception as exc:
        logger.debug("Не удалось опубликовать событие изменения заявки в Redis: %s", exc)


def notify_ticket_change() -> None:
    """Оповестить всех слушателей о создании, обновлении или смене статуса заявки."""
    for cb in _ticket_change_listeners:
        try:
            cb()
        except Exception:
            logger.exception("Ошибка при вызове слушателя изменений заявок")

    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_publish_ticket_change_to_redis())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    except RuntimeError:
        pass
