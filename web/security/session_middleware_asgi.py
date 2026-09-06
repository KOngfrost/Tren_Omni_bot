"""
Модуль безопасности: поддержка параллельных сессий (plain ASGI).

Использует уникальные session_id для каждого пользователя,
храня сессии в отдельных cookie: session_<session_id>.
session_id извлекается из query-параметра ?sid=<session_id>.

Работает поверх Starlette's SessionMiddleware:
- SessionMiddleware управляет CSRF-токеном в standard session cookie
- SessionAuthMiddleware управляет данными пользователя в отдельных cookie

ВАЖНО: Должен быть добавлен ПОСЛЕ SessionMiddleware в app.add_middleware(),
чтобы scope["session"] уже существовал при обработке запроса.
В Starlette порядок выполнения обратный порядку добавления, поэтому
SessionAuthMiddleware добавляется ПОСЛЕ (inner to) SessionMiddleware.
"""

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Хранилище сессий: {session_id: dict}
# Для production с несколькими workers рекомендуется использовать Redis.
_sessions: dict[str, dict[str, Any]] = {}
_SESSION_TTL = 3600  # 1 час


def _session_cookie_name(session_id: str) -> str:
    """Имя cookie для сессии."""
    return f"session_{session_id}"


def _get_session(session_id: str) -> dict[str, Any]:
    """Получить или создать сессию."""
    if session_id not in _sessions:
        _sessions[session_id] = {"_last_access": time.time()}
    else:
        _sessions[session_id]["_last_access"] = time.time()
    return _sessions[session_id]


def _extract_session_id_from_scope(scope: dict) -> str | None:
    """Извлечь session_id из query-параметра в ASGI scope."""
    query_string = scope.get("query_string", b"")
    if query_string:
        # Парсим query string вручную
        query_str = query_string.decode("utf-8")
        for param in query_str.split("&"):
            if param.startswith("sid="):
                sid = param[4:]
                if len(sid) >= 8:
                    return sid
    return None


class SessionAuthMiddleware:
    """Plain ASGI middleware для поддержки параллельных сессий.

    Позволяет нескольким пользователям быть авторизованным одновременно
    с разных аккаунтов/браузеров. Каждая сессия имеет уникальный session_id
    и хранится в отдельном cookie.

    Используется как plain ASGI app (не BaseHTTPMiddleware), чтобы
    корректно видеть request.scope["session"], установленный SessionMiddleware.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: dict, receive, send):
        # Обрабатываем только HTTP запросы
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Извлекаем session_id из query-параметра
        session_id = _extract_session_id_from_scope(scope)

        if session_id:
            store = _get_session(session_id)

            # Загружаем сессию из cookie
            cookie_name = _session_cookie_name(session_id)
            # Читаем cookies из scope["headers"]
            cookie_data = None
            headers = scope.get("headers", [])
            for name, value in headers:
                if name == cookie_name.encode():
                    cookie_data = value.decode()
                    break

            if cookie_data:
                try:
                    data = json.loads(cookie_data)
                    stored = {k: v for k, v in data.items() if k != "_last_access"}
                    store.update(stored)
                except Exception:
                    logger.warning("Не удалось загрузить сессию: %s", session_id[:8])

            # Копируем user данные в scope["session"] для совместимости с CSRF
            # scope["session"] уже существует, потому что SessionMiddleware
            # добавлен ПЕРЭД SessionAuthMiddleware (выполняется до него).
            if "session" in scope:
                session_store = scope["session"]
                if "user" in store:
                    session_store["user"] = store["user"]
                if "session_id" in store:
                    session_store["session_id"] = store["session_id"]
                # Добавляем _session_id в session store для доступа в маршрутах
                session_store["_session_id"] = session_id

            # Создаем response wrapper для установки cookie
            # ВАЖНО: Cookie должны быть установлены в http.response.start,
            # а не в http.response.body, потому что заголовки отправляются
            # до тела ответа.
            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    # Сохраняем сессию в cookie при наличии пользователя
                    if "user" in store:
                        cookie_value = (
                            f"{cookie_name}={json.dumps(store)}; "
                            "Path=/; HttpOnly; SameSite=Strict; Max-Age=3600"
                        )
                        headers = list(message.get("headers", []))
                        headers.append((b"set-cookie", cookie_value.encode()))
                        message["headers"] = headers
                await send(message)

            await self.app(scope, receive, send_wrapper)
        else:
            await self.app(scope, receive, send)
