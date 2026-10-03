"""Легковесная шина событий ядра для уведомления веб-панели и фоновых сервисов.

Позволяет подписчикам (например, SSE-маршруту /api/stream) получать мгновенные
оповещения об изменении статуса, создании или ответе на обращение без жесткой
связности между модулями бизнес-логики и веб-уровнем.
"""

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

_ticket_change_listeners: list[Callable[[], None]] = []


def register_ticket_change_listener(callback: Callable[[], None]) -> None:
    """Зарегистрировать функцию обратного вызова при изменении заявок."""
    if callback not in _ticket_change_listeners:
        _ticket_change_listeners.append(callback)


def unregister_ticket_change_listener(callback: Callable[[], None]) -> None:
    """Удалить слушателя изменений заявок."""
    if callback in _ticket_change_listeners:
        _ticket_change_listeners.remove(callback)


def notify_ticket_change() -> None:
    """Оповестить всех слушателей о создании, обновлении или смене статуса заявки."""
    for cb in _ticket_change_listeners:
        try:
            cb()
        except Exception:
            logger.exception("Ошибка при вызове слушателя изменений заявок")
