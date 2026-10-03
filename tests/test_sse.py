"""Тесты SSE-эндпоинта (web/routes/sse.py).

Проверяет:
- Неавторизованный доступ возвращает 401
- trigger_sse_update корректно сигнализирует asyncio.Event
- _build_ticket_item формирует правильные словари
- _build_recent_ticket формирует правильные словари
"""

from unittest.mock import MagicMock

from web.routes.sse import (
    _build_recent_ticket,
    _build_ticket_item,
    sse_wakeup_event,
    trigger_sse_update,
)


def test_trigger_sse_update():
    """trigger_sse_update устанавливает asyncio.Event."""
    sse_wakeup_event.clear()
    assert not sse_wakeup_event.is_set()
    trigger_sse_update()
    assert sse_wakeup_event.is_set()
    sse_wakeup_event.clear()


def test_build_ticket_item_new():
    """_build_ticket_item формирует элемент для новой заявки."""
    ticket = MagicMock()
    ticket.id = 42
    ticket.response_text = None
    ticket.department_id = 1
    ticket.department = MagicMock()
    ticket.department.name = "IT"
    ticket.description = "Тестовая заявка"
    ticket.topic = "Сломался принтер"
    ticket.created_at = None

    result = _build_ticket_item(ticket)

    assert result["type"] == "new_ticket"
    assert result["id"] == 42
    assert result["department"] == "IT"
    assert result["is_general"] is False
    assert result["url"] == "/tickets/?open=42"
    assert result["text"] == "Тестовая заявка"


def test_build_ticket_item_reply():
    """_build_ticket_item формирует элемент для ответа студента."""
    ticket = MagicMock()
    ticket.id = 10
    ticket.response_text = "Спасибо, исправлено"
    ticket.department_id = None
    ticket.department = None
    ticket.description = "Описание"
    ticket.topic = "Тема"
    ticket.created_at = None

    result = _build_ticket_item(ticket)

    assert result["type"] == "student_reply"
    assert result["department"] == "Без отдела"
    assert result["is_general"] is True
    assert result["general_label"] == "Общее обращение"


def test_build_recent_ticket():
    """_build_recent_ticket формирует JSON-представление заявки."""
    from core.models import TicketStatus

    ticket = MagicMock()
    ticket.id = 7
    ticket.topic = "Заявка на ремонт"
    ticket.status = TicketStatus.NEW
    ticket.department = MagicMock()
    ticket.department.name = "АХО"
    ticket.created_at = None

    result = _build_recent_ticket(ticket)

    assert result["id"] == 7
    assert result["topic"] == "Заявка на ремонт"
    assert result["status_badge"] == "badge-new"
    assert result["status_label"] == "Новая"
    assert result["department"] == "АХО"


def test_build_recent_ticket_completed():
    """_build_recent_ticket для завершённой заявки."""
    from core.models import TicketStatus

    ticket = MagicMock()
    ticket.id = 8
    ticket.topic = None
    ticket.status = TicketStatus.COMPLETED
    ticket.department = None
    ticket.created_at = None

    result = _build_recent_ticket(ticket)

    assert result["topic"] == "Без темы"
    assert result["status_badge"] == "badge-completed"
    assert result["status_label"] == "Решена"
    assert result["department"] == "Без отдела"


def test_sse_stream_unauthorized(web_client):
    """SSE-эндпоинт отвечает 401 при отсутствии авторизации."""
    response = web_client.get("/api/stream")
    assert response.status_code == 401


def test_websocket_unauthorized(web_client):
    """WebSocket-эндпоинт отклоняет неавторизованные соединения."""
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc_info, web_client.websocket_connect("/ws"):
        pass
    assert exc_info.value.code == 1008


def test_core_events_dispatch():
    """Тест регистрации и уведомления подписчиков в core/events.py."""
    from core.events import (
        notify_ticket_change,
        register_ticket_change_listener,
        unregister_ticket_change_listener,
    )

    called = []

    def sample_callback():
        called.append(True)

    register_ticket_change_listener(sample_callback)
    notify_ticket_change()
    assert len(called) == 1

    unregister_ticket_change_listener(sample_callback)
    notify_ticket_change()
    assert len(called) == 1
