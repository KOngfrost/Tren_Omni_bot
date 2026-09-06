"""
Модуль безопасности: поддержка параллельных сессий.

Использует уникальные session_id для каждого пользователя,
храня сессии в отдельных cookie: session_<session_id>.
session_id извлекается из query-параметра ?sid=<session_id>.

Работает поверх Starlette's SessionMiddleware:
- SessionMiddleware управляет CSRF-токеном в standard session cookie
- SessionAuthMiddleware управляет данными пользователя в отдельных cookie
  и копирует их в request.scope["session"] для совместимости с CSRF
"""

import json
import logging
import secrets
import time
from typing import Any

from fastapi import Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Хранилище сессий: {session_id: dict}
_sessions: dict[str, dict[str, Any]] = {}
_SESSION_TTL = 3600  # 1 час


def _session_cookie_name(session_id: str) -> str:
    """Имя cookie для сессии."""
    return f"session_{session_id}"


def _extract_session_id(request: Request) -> str | None:
    """Извлечь session_id из query-параметра ?sid=<session_id>."""
    sid = request.query_params.get("sid")
    if sid and len(sid) >= 8:
        return sid
    return None


def _get_session(session_id: str) -> dict[str, Any]:
    """Получить или создать сессию."""
    if session_id not in _sessions:
        _sessions[session_id] = {"_last_access": time.time()}
    else:
        _sessions[session_id]["_last_access"] = time.time()
    return _sessions[session_id]


def _cleanup_expired_sessions() -> None:
    """Очистить истёкшие сессии."""
    now = time.time()
    expired = [
        sid for sid, store in _sessions.items()
        if now - store.get("_last_access", 0) > _SESSION_TTL
    ]
    for sid in expired:
        del _sessions[sid]
        logger.debug("Сессия удалена: %s", sid[:8])


class SessionAuthMiddleware(BaseHTTPMiddleware):
    """Middleware для поддержки параллельных сессий.

    Работает поверх Starlette's SessionMiddleware:
    - SessionMiddleware управляет CSRF-токеном в standard session cookie
    - SessionAuthMiddleware управляет данными пользователя в отдельных cookie

    Каждый пользователь получает уникальный session_id, который используется
    для имени cookie: session_<session_id>.
    session_id передаётся через query-параметр ?sid=<session_id>.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        session_id = _extract_session_id(request)

        if session_id:
            store = _get_session(session_id)

            # Загружаем сессию из cookie
            cookie_name = _session_cookie_name(session_id)
            cookie_data = request.cookies.get(cookie_name)

            if cookie_data:
                try:
                    data = json.loads(cookie_data)
                    # Копируем данные в store, сохраняя _last_access
                    stored = {k: v for k, v in data.items() if k != "_last_access"}
                    store.update(stored)
                except Exception:
                    logger.warning("Не удалось загрузить сессию: %s", session_id[:8])

            # Копируем данные в request.scope["session"] для совместимости с CSRF
            if "session" in request.scope:
                session_store = request.scope["session"]
                # Копируем user данные, сохраняя CSRF-токен
                if "user" in store:
                    session_store["user"] = store["user"]
                if "session_id" in store:
                    session_store["session_id"] = store["session_id"]
            request.state.session_id = session_id
        else:
            request.state.session_id = None

        response = await call_next(request)

        # Сохраняем сессию в cookie
        if session_id and "user" in store:
            cookie_name = _session_cookie_name(session_id)
            data = {k: v for k, v in store.items() if k != "_last_access"}
            response.set_cookie(
                cookie_name,
                json.dumps(data),
                max_age=3600,
                httponly=True,
                samesite="strict",
                path="/",
            )

        return response
