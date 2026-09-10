"""
CSRF-защита для FastAPI-приложения.

Генерирует и проверяет CSRF-токены через сессию.
Токен хранится в сессии и передаётся одним из способов:
- скрытое поле формы `csrf_token`;
- заголовок `x-csrf-token`;
- поле `csrf_token` в JSON-теле (для API-запросов).

Проверка выполняется для ВСЕХ изменяющих методов (POST/PUT/PATCH/DELETE),
включая /auth/login (защита от login-CSRF). JSON-запросы больше НЕ
пропускаются без токена — раньше это была дыра в защите.
"""

import json
import secrets

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

CSRF_SESSION_KEY = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"
CSRF_FORM_FIELD = "csrf_token"


class CSRFMiddleware(BaseHTTPMiddleware):
    """Middleware для генерации CSRF-токена и проверки изменяющих запросов."""

    async def dispatch(self, request: Request, call_next):
        # GET/HEAD/OPTIONS — генерируем токен, если его нет в сессии
        if request.method in ("GET", "HEAD", "OPTIONS"):
            if CSRF_SESSION_KEY not in request.session:
                request.session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)
            response = await call_next(request)
            return response

        # POST/PUT/DELETE/PATCH — токен обязателен и должен совпадать с сессией
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            token_from_session: str | None = request.session.get(CSRF_SESSION_KEY)
            submitted = await self._extract_token(request)

            if not token_from_session or not submitted:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Отсутствует CSRF-токен. Обновите страницу и повторите."},
                )

            if not secrets.compare_digest(submitted, token_from_session):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Недействительный CSRF-токен"},
                )

            response = await call_next(request)
            return response

        # Другие методы — пропускаем
        response = await call_next(request)
        return response

    async def _extract_token(self, request: Request) -> str | None:
        """Взять токен из заголовка, формы или JSON-тела (не расходуя body)."""
        header_token = request.headers.get(CSRF_HEADER_NAME)
        if header_token:
            return header_token

        content_type = request.headers.get("content-type", "").lower()

        if "application/json" in content_type:
            return await self._token_from_json(request)

        if (
            "multipart/form-data" in content_type
            or "application/x-www-form-urlencoded" in content_type
        ):
            if "application/x-www-form-urlencoded" in content_type:
                return await self._token_from_raw_form(request)
            try:
                form = await request.form()
                value = form.get(CSRF_FORM_FIELD)
                return str(value) if value else None
            except Exception:
                # Форма не парсится (например, уже прочитана) — пробуем сырой body
                return await self._token_from_raw_form(request)

        # Неизвестный media type — пробуем распарсить как JSON, затем как форму
        json_token = await self._token_from_json(request)
        if json_token:
            return json_token
        try:
            form = await request.form()
            value = form.get(CSRF_FORM_FIELD)
            return str(value) if value else None
        except Exception:
            return None

    async def _token_from_json(self, request: Request) -> str | None:
        """Извлечь csrf_token из JSON-тела и восстановить body для обработчиков.

        БЕЗОПАСНОСТЬ: обрабатывает edge-case, когда body уже прочитан
        другим middleware (request._body уже установлен).
        """
        try:
            # Если body уже прочитан другим middleware — используем кеш
            raw = getattr(request, "_body", None)
            if raw is None:
                raw = await request.body()
                if not raw:
                    return None
                request._body = raw

            data = json.loads(raw)
            token = data.get(CSRF_FORM_FIELD) if isinstance(data, dict) else None

            # Восстанавливаем уже прочитанное тело, чтобы нижестоящие
            # обработчики могли снова вызвать request.json()
            if isinstance(data, dict):
                request._json = data
            return str(token) if token else None
        except Exception:
            return None

    async def _token_from_raw_form(self, request: Request) -> str | None:
        """Для форм, чей body уже прочитан — парсим вручную (fallback)."""
        try:
            raw = await request.body()
            request._body = raw
            from urllib.parse import parse_qs

            if raw.startswith(b"--"):
                # multipart из уже прочитанного body восстановить сложно;
                # таких кейсов в приложении нет — форма всегда доступна первой.
                return None
            params = parse_qs(raw.decode("utf-8", errors="replace"))
            values = params.get(CSRF_FORM_FIELD, [])
            return values[0] if values else None
        except Exception:
            return None


def get_csrf_token(request: Request) -> str:
    """Получить текущий CSRF-токен из сессии."""
    return request.session.get(CSRF_SESSION_KEY, "")


def rotate_csrf_token(request: Request) -> str:
    """
    Повернуть CSRF-токен и вернуть новый.
    Вызывается после чувствительных операций (логин, смена роли).
    """
    new_token = secrets.token_urlsafe(32)
    request.session[CSRF_SESSION_KEY] = new_token
    return new_token


def validate_csrf(request: Request, token: str) -> bool:
    """Валидировать CSRF-токен из формы/заголовка/JSON."""
    token_from_session: str | None = request.session.get(CSRF_SESSION_KEY)
    if not token_from_session:
        return False
    return secrets.compare_digest(token, token_from_session)
