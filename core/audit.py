"""
Полный аудит действий в веб-панели.

Требование: журнал должен фиксировать АБСОЛЮТНО все действия, а не только
входы и ответы на заявки. Здесь два механизма:

1. `AuditMiddleware` — классифицирует каждый обработанный запрос и складывает
   событие в буфер `enqueue_audit()` (без обращения к БД прямо в запросе).
2. `record_action()` — точечная запись для событий вне HTTP-потока
   (фоновые задачи, авто-удаление временных админов).

События из буфера выгружает в БД фоновый цикл `audit_flush_loop()`,
запущенный в lifespan приложения (см. web/main.py), пакетами раз в несколько
секунд. При остановке приложения накопленное дописывается через
`flush_audit_queue()`.

Почему буфер, а не запись прямо в middleware:
- запись «в лоб» конкурирует за соединение с обработчиком (тот держит его до
  конца ответа) — на пуле малого размера это взаимная блокировка;
- отдельная задача на каждый запрос живёт в другом контексте событийного цикла
  и может утащить соединение в чужой цикл;
- пакетная вставка дешевле, чем INSERT на каждое обращение;
- ошибка аудита не должна превращать рабочий ответ в 500;
- служебные пути (/static, /health) исключены, иначе журнал утонет в шуме.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from core.models import Log

logger = logging.getLogger(__name__)


def _session_maker():
    """Получить актуальный session maker.

    Обращаемся к core.database в момент вызова, а не к импортированному
    имени: так подмена соединения в тестах (monkeypatch по атрибуту модуля)
    применяется автоматически. Иначе запись аудита в тестах попыталась бы
    подключиться к боевому PostgreSQL.
    """
    from core import database

    return database.async_session_maker()


# Типы субъектов действий.
ACTOR_WEB = "web"
ACTOR_ANONYMOUS = "anonymous"
ACTOR_SYSTEM = "system"

# Служебные пути: журналировать их бессмысленно.
AUDIT_SKIP_PREFIXES: tuple[str, ...] = (
    "/static/",
    "/favicon.ico",
    "/health",
    "/metrics",
    "/legal/",
    "/_",
)

# Методы, меняющие состояние.
MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Человекочитаемые подписи действий. Порядок важен: первое совпадение
# выигрывает, более специфичные правила идут раньше общих.
_ROUTE_ACTIONS: tuple[tuple[str, str], ...] = (
    ("/auth/login", "Вход в панель"),
    ("/auth/logout", "Выход из панели"),
    ("/auth/2fa", "Подтверждение входа (2FA)"),
    ("/admin/admins", "Управление администраторами"),
    ("/admin/departments", "Управление отделами"),
    ("/tickets/", "Работа с заявками"),
    ("/partnerships", "Партнёрские заявки"),
    ("/knowledge", "База знаний"),
    ("/faq", "FAQ"),
    ("/events", "Мероприятия"),
    ("/logs", "Журнал аудита"),
    ("/api/counters", "API: счётчики уведомлений"),
    ("/api/departments", "API: отделы"),
    ("/api/", "API-запрос"),
    ("/settings/", "Настройки"),
    ("/maintenance", "Режим техработ"),
)


def classify_path(path: str) -> str:
    """Человекочитаемое название действия по пути запроса."""
    for prefix, label in _ROUTE_ACTIONS:
        if path.startswith(prefix):
            return label
    return "Просмотр страницы"


def should_audit(path: str) -> bool:
    """Журналировать ли этот путь."""
    return not any(path.startswith(p) for p in AUDIT_SKIP_PREFIXES)


async def record_action(
    *,
    action: str,
    details: str | None = None,
    actor_type: str = ACTOR_WEB,
    actor_name: str | None = None,
    http_method: str | None = None,
    path: str | None = None,
    status_code: int | None = None,
    duration_ms: int | None = None,
    ip_address: str | None = None,
    is_mutation: bool = False,
) -> None:
    """Записать одно событие в журнал аудита.

    Ошибки не поднимаются наружу: невозможность записать в журнал не должна
    превращаться в ошибку для пользователя.
    """
    try:
        async with _session_maker() as session:
            session.add(
                Log(
                    action=action,
                    details=details,
                    actor_type=actor_type,
                    actor_name=actor_name or None,
                    http_method=http_method,
                    path=path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    ip_address=ip_address,
                    is_mutation=is_mutation,
                )
            )
            await session.commit()
    except Exception:
        logger.warning("Не удалось записать событие аудита: %s", action, exc_info=True)


class AuditMiddleware(BaseHTTPMiddleware):
    """Регистрирует каждый обработанный запрос веб-панели в журнале аудита.

    Событие складывается в буфер (`enqueue_audit`) без обращения к БД,
    поэтому middleware не задерживает ответ и не конкурирует за соединение
    с обработчиком запроса. Запись в журнал выполняет фоновый цикл
    `audit_flush_loop()`, запущенный в lifespan приложения.
    """

    def __init__(self, app, background: Callable[[Any], None] | None = None) -> None:
        super().__init__(app)
        # Параметр оставлен для совместимости с тестами, которые подменяют
        # обработчик событий аудита.
        self._background = background

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if not should_audit(path):
            return await call_next(request)

        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = int((time.perf_counter() - started) * 1000)

        # Логин берём из сессии ПОСЛЕ обработки запроса: require_auth
        # кладёт канонический user в request.session.
        actor_name: str | None = None
        try:
            user = request.session.get("user")
            if isinstance(user, dict):
                actor_name = str(user.get("username") or "") or None
        except Exception:
            actor_name = None

        method = request.method.upper()
        payload: dict[str, Any] = {
            "action": classify_path(path),
            "details": f"{method} {path} -> {response.status_code}",
            "actor_type": ACTOR_WEB if actor_name else ACTOR_ANONYMOUS,
            "actor_name": actor_name,
            "http_method": method[:8],
            "path": path[:255],
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "ip_address": _client_ip(request),
            "is_mutation": method in MUTATION_METHODS,
        }

        if self._background is not None:
            self._background(payload)
        else:
            enqueue_audit(payload)

        return response


def _client_ip(request: Request) -> str | None:
    """IP клиента с учётом обратного прокси (X-Forwarded-For)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


# ==========================================
# Буфер событий и фоновый сброс в БД
# ==========================================
# Почему буфер, а не запись прямо в middleware:
# - запись «в лоб» внутри запроса конкурирует за соединение с обработчиком
#   (тот держит соединение до конца ответа) — на пуле малого размера это
#   приводит к взаимной блокировке;
# - отдельная задача на каждый запрос «уезжает» в другой контекст цикла и
#   может утащить за собой соединение;
# - пакетная вставка дешевле, чем INSERT на каждое обращение.
# Буфер ограничен: при переполнении самое старое событие отбрасывается с
# предупреждением, чтобы панель никогда не блокировалась из-за аудита.
AUDIT_QUEUE_MAX = 5000
AUDIT_FLUSH_INTERVAL_SECONDS = 5.0
AUDIT_BATCH_SIZE = 200

_audit_queue: deque[dict[str, Any]] = deque(maxlen=AUDIT_QUEUE_MAX)
_queue_lock = threading.Lock()
_dropped_events = 0


def enqueue_audit(payload: dict[str, Any]) -> None:
    """Поставить событие в буфер аудита (никогда не блокирует вызывающего)."""
    global _dropped_events

    with _queue_lock:
        if len(_audit_queue) == _audit_queue.maxlen:
            _dropped_events += 1
            if _dropped_events % 100 == 1:
                logger.warning(
                    "Буфер аудита переполнен: часть событий не записана (всего потеряно %d)",
                    _dropped_events,
                )
            # deque с maxlen сам вытеснит самый старый элемент.
        _audit_queue.append(payload)


def drain_audit_queue(limit: int = AUDIT_BATCH_SIZE) -> list[dict[str, Any]]:
    """Забрать из буфера не более `limit` событий."""
    with _queue_lock:
        taken: list[dict[str, Any]] = []
        while _audit_queue and len(taken) < limit:
            taken.append(_audit_queue.popleft())
        return taken


def pending_audit_count() -> int:
    """Сколько событий ещё не записано (для диагностики и тестов)."""
    with _queue_lock:
        return len(_audit_queue)


def reset_audit_queue() -> None:
    """Очистить буфер (используется в тестах для изоляции)."""
    global _dropped_events

    with _queue_lock:
        _audit_queue.clear()
        _dropped_events = 0


async def flush_audit_queue() -> int:
    """Записать накопленные события в журнал. Возвращает число записанных."""
    events = drain_audit_queue()
    if not events:
        return 0

    try:
        async with _session_maker() as session:
            session.add_all([Log(**event) for event in events])
            await session.commit()
        return len(events)
    except Exception:
        logger.warning("Не удалось записать пачку аудита (%d событий)", len(events), exc_info=True)
        return 0


async def audit_flush_loop() -> None:
    """Фоновый цикл выгрузки буфера аудита в БД.

    Запускается один раз в lifespan приложения, поэтому живёт в том же
    событийном цикле, что и остальные фоновые задачи.
    """
    while True:
        try:
            await flush_audit_queue()
        except Exception:
            logger.exception("Ошибка цикла выгрузки аудита")
        await asyncio.sleep(AUDIT_FLUSH_INTERVAL_SECONDS)


__all__ = [
    "ACTOR_ANONYMOUS",
    "ACTOR_SYSTEM",
    "ACTOR_WEB",
    "AuditMiddleware",
    "audit_flush_loop",
    "classify_path",
    "drain_audit_queue",
    "enqueue_audit",
    "flush_audit_queue",
    "pending_audit_count",
    "record_action",
    "reset_audit_queue",
    "should_audit",
]
