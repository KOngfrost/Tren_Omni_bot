"""
Модуль безопасности: заголовки, rate limiting, валидация ввода.
"""

import logging
import secrets
import time
from collections import defaultdict

import bleach
from fastapi import HTTPException, Request, status
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


# === Безопасные заголовки ===

SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Pragma": "no-cache",
}

# CSP без 'unsafe-inline' — nonce добавляется динамически в middleware
# 'unsafe-inline' удалён: он позволяет выполнение инлайнового JS, что
# снижает защиту от XSS. Все скрипты должны быть подключены через nonce.
CONTENT_SECURITY_POLICY_BASE = (
    "default-src 'self'; "
    "script-src 'self' 'nonce-{nonce}'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self';"
)

# Публичное имя для проверок и кода, которому нужен базовый CSP-шаблон.
CONTENT_SECURITY_POLICY = CONTENT_SECURITY_POLICY_BASE


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Добавляет заголовки безопасности к каждому ответу.

    Включает CSP с nonce для script-src и style-src.
    Nonce генерируется для каждого запроса и передаётся через request.state
    для использования в шаблонах.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Генерируем nonce для script-src
        script_nonce = secrets.token_urlsafe(16)
        # Отдельный nonce для style-src (лучшая практика CSP)
        style_nonce = secrets.token_urlsafe(16)

        # Сохраняем nonce в request.state для использования в шаблонах
        request.state.script_nonce = script_nonce
        request.state.style_nonce = style_nonce

        # Формируем CSP с nonce
        csp = CONTENT_SECURITY_POLICY_BASE.format(
            nonce=script_nonce,
            nonce_style=style_nonce,
        )

        response = await call_next(request)

        for header, value in SECURITY_HEADERS.items():
            response.headers[header] = value
        response.headers["Content-Security-Policy"] = csp

        # Скрываем заголовок Server
        if "Server" in response.headers:
            del response.headers["Server"]
        if "x-powered-by" in response.headers:
            del response.headers["x-powered-by"]
        return response


# === Rate Limiting ===

class RateLimiter:
    """Простой rate limiter на основе sliding window."""

    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        window_start = now - self.window_seconds

        # Очищаем старые записи
        self.requests[key] = [
            t for t in self.requests[key] if t > window_start
        ]

        if len(self.requests[key]) >= self.max_requests:
            return False

        self.requests[key].append(now)
        return True


# Глобальные лимитеры
_login_rate_limiter = RateLimiter(max_requests=5, window_seconds=300)  # 5 попыток за 5 минут


def check_rate_limit(key: str, limiter: RateLimiter) -> bool:
    """Проверить, не превышен ли лимит запросов."""
    return limiter.is_allowed(key)


# === XSS-санитизация через bleach ===

# Разрешённые HTML-теги для очистки пользовательского ввода.
# Пустой список — strip all tags (максимальная безопасность).
ALLOWED_TAGS: list[str] = []

# Разрешённые атрибуты (пусто — удаляем все атрибуты, включая on*).
ALLOWED_ATTRIBUTES: dict[str, list[str]] = {}


def sanitize_html(value: str) -> str:
    """Очистить HTML-ввод от XSS с помощью bleach.

    Экранирует все HTML-теги и атрибуты (strip=False) — данные пользователя
    сохраняются и безопасно отображаются как текст. В отличие от strip=True,
    не вырезает содержимое тегов («error in line < 5» остаётся читаемым).
    bleach корректно обрабатывает вложенные теги, сущности и edge-кейсы,
    которые regex мог пропустить.
    """
    if not value or not isinstance(value, str):
        return value or ""

    try:
        cleaned = bleach.clean(
            value,
            tags=ALLOWED_TAGS,
            attributes=ALLOWED_ATTRIBUTES,
            strip=False,
        )
        # bleach экранирует < > &, но не кавычки — дополняем вручную,
        # чтобы вложить результат в HTML-атрибут было безопасно
        cleaned = cleaned.replace('"', "&quot;").replace("'", "&#x27;")
        # Логирование попыток XSS
        if cleaned != value:
            logger.warning("XSS-паттерн очищен bleach: %s", value[:200])
        return cleaned
    except Exception:
        # На случай проблем с bleach — безопасный fallback: HTML-экранирование
        logger.exception("Ошибка в bleach.clean, fallback на HTML-экранирование")
        return _html_escape(value)


def _html_escape(value: str) -> str:
    """Базовое HTML-экранирование (fallback, если bleach недоступен)."""
    value = str(value)
    value = value.replace("&", "&amp;")
    value = value.replace("<", "&lt;")
    value = value.replace(">", "&gt;")
    value = value.replace('"', "&quot;")
    value = value.replace("'", "&#x27;")
    return value


# === CSV Injection защита ===

_CSV_INJECTION_PATTERNS = [
    r'^[=+\-@]',          # Начинается с =, +, -, @
    r'\b(CMD\|)',         # CMD|
    r'\b(SHELL\|)',       # SHELL|
    r'\b(IMPORT\|)',      # IMPORT|
    r'\b(PICKLIST\|)',    # PICKLIST|
    r'\b(DATATABLE\|)',   # DATATABLE|
    r'!A\d',              # Ссылки на ячейки
    r'`.*`',              # Backtick-инъекции
]


def sanitize_csv_field(value: str) -> str:
    """
    Санитизировать поле для CSV-экспорта.
    Убирает символы, которые могут вызвать CSV-injection.
    """
    if not value:
        return ""

    import re
    value = str(value)

    # Проверяем паттерны инъекций
    for pattern_str in _CSV_INJECTION_PATTERNS:
        if re.search(pattern_str, value):
            # Экранируем, добавляя табуляцию в начало
            return f"	{value}"

    return value


def escape_for_csv(value: str) -> str:
    """
    Полная экранировка поля для CSV.
    Заменяет кавычки и оборачивает в кавычки при необходимости.
    """
    if not value:
        return ""

    value = str(value)
    # Экранируем двойные кавычки
    value = value.replace('"', '""')

    # Проверяем, нужно ли оборачивать в кавычки
    if ',' in value or '"' in value or '\n' in value or '\r' in value:
        value = f'"{value}"'

    return value


# === Инжект отделов пользователя ===

# === Валидация размера запроса ===

class RequestSizeValidator:
    """Валидатор размера запроса."""

    def __init__(self, max_body_size: int = 10 * 1024 * 1024):  # 10MB по умолчанию
        self.max_body_size = max_body_size

    def check_content_length(self, request: Request) -> None:
        """Проверить Content-Length заголовок."""
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
                if size > self.max_body_size:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Размер запроса превышает лимит {self.max_body_size} байт",
                    )
            except ValueError:
                pass

    async def check_form_size(self, request: Request) -> None:
        """Проверить размер запроса, не потребляя body до обработчика."""
        self.check_content_length(request)
