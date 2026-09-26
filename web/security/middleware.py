"""
Модуль безопасности: заголовки, rate limiting, валидация ввода.
"""

import html
import json
import logging
import secrets
import time
from collections import defaultdict
from contextvars import ContextVar

from fastapi import HTTPException, Request, status
from fastapi.responses import Response
from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Nonce текущего запроса для inline <script>/<style>.
# ContextVar используется потому, что шаблоны рендерятся и вне HTTP-запроса
# (тесты, офлайн-генерация HTML), где request.state недоступен.
_csp_nonces: ContextVar[dict[str, str] | None] = ContextVar("csp_nonces", default=None)


def csp_nonce(kind: str = "script") -> str:
    """Вернуть nonce ('script' или 'style') для текущего запроса.

    Пустая строка, если middleware безопасности не выполнялся: тогда у
    inline-блока просто не будет атрибута nonce (страница остаётся рабочей,
    CSP заблокирует только этот блок).
    """
    nonces = _csp_nonces.get()
    return nonces.get(kind, "") if nonces else ""


# === Безопасные заголовки ===

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-XSS-Protection": "0",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
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
    "script-src 'self' 'nonce-{nonce}' https://telegram.org; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "frame-ancestors 'self' https://*.telegram.org https://telegram.org; "
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
        # Единая точка доступа для шаблонов: csp_nonce в request.state
        # (web.templating прокидывает её в контекст каждого TemplateResponse)
        request.state.csp_nonce = {"script": script_nonce, "style": style_nonce}
        # и в ContextVar — шаблоны могут рендериться без Request в контексте
        nonce_token = _csp_nonces.set({"script": script_nonce, "style": style_nonce})

        # Формируем CSP с nonce
        csp = CONTENT_SECURITY_POLICY_BASE.format(
            nonce=script_nonce,
        )

        try:
            response = await call_next(request)
        finally:
            _csp_nonces.reset(nonce_token)

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
    """Простой rate limiter на основе sliding window (in-memory).

    WARNING: НЕ подходит для многопроцессного запуска (uvicorn --workers > 1).
    Используйте DBRateLimiter для production с несколькими воркерами.
    """

    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        window_start = now - self.window_seconds

        # Очищаем старые записи
        self.requests[key] = [t for t in self.requests[key] if t > window_start]

        if len(self.requests[key]) >= self.max_requests:
            return False

        self.requests[key].append(now)
        return True


class DBRateLimiter:
    """DB-backed rate limiter для многопроцессного окружения.

    Использует таблицу crud_attempts (или login_attempts) для хранения
    состояния, что обеспечивает единый лимит при нескольких uvicorn-workers.

    Пример использования:
        limiter = DBRateLimiter("crud_attempts", max_requests=20, window_seconds=300)
        if not await limiter.is_allowed(session, ip, "ticket_status_change"):
            raise HTTPException(429, "Too many requests")
    """

    ALLOWED_TABLES = frozenset({"crud_attempts", "login_attempts"})

    def __init__(self, table_name: str, max_requests: int, window_seconds: int):
        if table_name not in self.ALLOWED_TABLES:
            raise ValueError(f"Недопустимая таблица rate-limiter: {table_name!r}")
        self.table_name = table_name
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def is_allowed(self, session, ip: str, action: str) -> bool:
        """Проверить, не превышен ли лимит для IP+action."""
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import text

        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=self.window_seconds)

        # Параметризованные запросы с явным указанием таблицы из белого списка.
        # Имя таблицы подставляется через безопасный форматный строковый шаблон,
        # а не через f-string, чтобы исключить риск SQL-инъекции при будущих
        # изменениях логики валидации.
        if self.table_name == "crud_attempts":
            count_stmt = text(
                "SELECT COUNT(*) FROM {table} "  # noqa: UP032
                "WHERE ip = :ip AND action = :action AND attempted_at >= :cutoff".format(
                    table=self.table_name
                )
            )
            count_result = await session.execute(
                count_stmt, {"ip": ip, "action": action, "cutoff": cutoff}
            )
            count = int(count_result.scalar() or 0)

            if count >= self.max_requests:
                return False

            # Записать новую попытку
            insert_stmt = text(
                "INSERT INTO {table} (ip, action, attempted_at) "  # noqa: UP032
                "VALUES (:ip, :action, :now)".format(table=self.table_name)
            )
            await session.execute(insert_stmt, {"ip": ip, "action": action, "now": now})
        else:
            count_stmt = text(
                "SELECT COUNT(*) FROM {table} "  # noqa: UP032
                "WHERE ip = :ip AND attempted_at >= :cutoff".format(table=self.table_name)
            )
            count_result = await session.execute(count_stmt, {"ip": ip, "cutoff": cutoff})
            count = int(count_result.scalar() or 0)

            if count >= self.max_requests:
                return False

            # Записать новую попытку
            insert_stmt = text(
                "INSERT INTO {table} (ip, attempted_at, success) VALUES (:ip, :now, false)".format(  # noqa: UP032
                    table=self.table_name
                )
            )
            await session.execute(insert_stmt, {"ip": ip, "now": now})

        try:
            await session.commit()
            return True
        except Exception:
            # При ошибке БД — блокируем запрос (fail-close), чтобы не пропускать
            # потенциально вредоносные действия при проблемах с бэкендом.
            # Логируем полный контекст для последующего анализа.
            logger.error(
                "DBRateLimiter: ошибка при фиксации лимита для таблицы %s, "
                "IP %s, action %s — запрос блокируется (fail-close)",
                self.table_name,
                ip,
                action,
                exc_info=True,
            )
            return False


# SEC-11: In-memory RateLimiter устарел и оставлен исключительно для
# обратной совместимости импортов. В production для защиты от подбора паролей
# и спама запросов используется исключительно DBRateLimiter (login_attempts,
# crud_attempts), который корректно синхронизирует лимиты между воркерами.
def check_rate_limit(key: str, limiter: RateLimiter) -> bool:
    """Проверить, не превышен ли лимит запросов (deprecated)."""
    return limiter.is_allowed(key)


# === XSS-санитизация через stdlib html.escape ===


def sanitize_html(value: str) -> str:
    """Очистить пользовательский ввод от XSS.

    Удаляет опасные нуль-байты (\x00) и управляющие символы,
    экранирует все HTML-символы (<, >, &, ", ') с помощью стандартного модуля html.
    """
    if not value or not isinstance(value, str):
        return value or ""

    # Удаляем нуль-байты и опасные управляющие символы (оставляем переводы строк и табуляцию)
    cleaned = "".join(ch for ch in value if ch in ("\n", "\r", "\t") or ord(ch) >= 32)
    escaped = html.escape(cleaned, quote=True).replace("'", "&#x27;")
    if escaped != value:
        logger.debug("Пользовательский ввод экранирован: %s", value[:100])
    return escaped


# === CSV Injection защита ===

_CSV_INJECTION_PATTERNS = [
    r"^[=+\-@]",  # Начинается с =, +, -, @
    r"\b(CMD\|)",  # CMD|
    r"\b(SHELL\|)",  # SHELL|
    r"\b(IMPORT\|)",  # IMPORT|
    r"\b(PICKLIST\|)",  # PICKLIST|
    r"\b(DATATABLE\|)",  # DATATABLE|
    r"!A\d",  # Ссылки на ячейки
    r"`.*`",  # Backtick-инъекции
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
            # Экранируем, добавляя знак табуляции в начало (стандартный маркер текста в электронных таблицах)
            return f"\t{value}"

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
    if "," in value or '"' in value or "\n" in value or "\r" in value:
        value = f'"{value}"'

    return value


# === Инжект отделов пользователя ===

# === Валидация размера запроса ===


class RequestSizeValidator:
    """Валидатор размера запроса (ранний контроль заголовка Content-Length).

    Ошибка #4 — защита от атак через потоковую передачу данных:
    контроль одного заголовка Content-Length недостаточен — запрос с
    ``Transfer-Encoding: chunked`` приходит БЕЗ этого заголовка. Фактический
    подсчёт прочитанных байтов при потоковом получении тела и принудительный
    разрыв соединения с 413 выполняет зарегистрированный ASGI-слой
    ``RequestSizeLimitMiddleware`` (см. ниже): он обёртывает receive, буферизует
    ограниченным окном и прерывает запрос ответом 413, как только суммарный
    размер потока превысит лимит (10 МБ).

    Настоящий класс сохранён как первый (оборонительный) рубеж в
    ``@app.middleware("http") validate_request_size`` (web/main.py):
    - ранний отказ 413 по объявленному Content-Length, ещё до входа
      во внутренние слои сессии/CSRF;
    - совместимость с прямыми unit-тестами (mock request).
    """

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


class RequestSizeLimitMiddleware:
    """ASGI-middleware: жёсткий лимит размера тела запроса.

    Проверки заголовка ``Content-Length`` недостаточно: запрос с
    ``Transfer-Encoding: chunked`` приходит без этого заголовка и полностью
    обходит ограничение. Поэтому middleware считает ФАКТИЧЕСКИЕ байты тела:

    - ``Content-Length`` объявлен и больше лимита — отказ 413 без чтения тела;
    - ``Content-Length`` отсутствует (chunked/HTTP/2) — тело читается
      ограниченным буфером: как только суммарный размер превышает лимит,
      запрос прерывается ответом 413; иначе буфер передаётся дальше
      через «воспроизводящий» receive, и обработчик видит тело целиком.
    """

    def __init__(self, app: ASGIApp, max_body_size: int = 10 * 1024 * 1024) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def _reject(self, send: Send) -> None:
        """Ответить 413 без раскрытия деталей запроса."""
        payload = json.dumps(
            {"detail": f"Размер запроса превышает лимит {self.max_body_size} байт"},
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(payload)).encode("latin-1")),
                    (b"connection", b"close"),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload, "more_body": False})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)

        declared = headers.get("content-length")
        if declared:
            try:
                if int(declared) > self.max_body_size:
                    logger.warning(
                        "413: объявлен размер тела %s байт (лимит %s)",
                        declared,
                        self.max_body_size,
                    )
                    await self._reject(send)
                    return
            except ValueError:
                pass
            await self.app(scope, receive, send)
            return

        if headers.get("transfer-encoding") is None and scope.get("method") in {
            "GET",
            "HEAD",
            "OPTIONS",
            "TRACE",
        }:
            # Тела нет и оно не заявлено — проверять нечего.
            await self.app(scope, receive, send)
            return

        buffer = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # Клиент отключился до завершения передачи
                await self.app(scope, _drained_receive, send)
                return
            buffer.extend(message.get("body", b""))
            if len(buffer) > self.max_body_size:
                logger.warning(
                    "413: фактический размер тела превысил лимит %s байт (chunked)",
                    self.max_body_size,
                )
                await self._reject(send)
                return
            if not message.get("more_body", False):
                break

        body = bytes(buffer)
        delivered = False

        async def replay_receive() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay_receive, send)


async def _drained_receive() -> Message:
    """Receive для случая «клиент уже отключился»."""
    return {"type": "http.disconnect"}


class MaintenanceMiddleware(BaseHTTPMiddleware):
    """Перехватчик режима технических работ для веб-панели.

    Когда режим техработ активен:
    - Пропускает служебные маршруты: /static/*, /health, /metrics, /favicon.ico, /maintenance.
    - Пропускает авторизованных суперадминистраторов (role in ('SUPERADMIN', 'superadmin')),
      выставляя request.state.maintenance_active = True для отображения служебного баннера.
    - Для остальных пользователей возвращает стилизованную страницу maintenance.html (HTTP 503)
      или JSON-ответ 503 для API с заголовком Retry-After: 300.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            path.startswith("/static/")
            or path in ("/health", "/metrics", "/favicon.ico", "/maintenance")
        ):
            return await call_next(request)

        from core.maintenance import get_maintenance_info, is_maintenance_mode

        if await is_maintenance_mode():
            request.state.maintenance_active = True

            # Маршруты авторизации доступны для возможности входа суперадминистратора
            if path.startswith("/auth/"):
                return await call_next(request)

            # Safely access session only if SessionMiddleware is present
            session_data = request.scope.get("session", {})
            user = session_data.get("user")
            is_superadmin = (
                isinstance(user, dict)
                and user.get("role") in ("SUPERADMIN", "superadmin")
            )
            if is_superadmin:
                return await call_next(request)

            accept = request.headers.get("accept", "")
            if path.startswith("/api/") or ("application/json" in accept and "text/html" not in accept):
                info = await get_maintenance_info()
                from starlette.responses import JSONResponse

                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "maintenance",
                        "detail": info.get("message", "Ведутся технические работы."),
                    },
                    headers={"Retry-After": "300"},
                )

            from core.config import settings
            from web.templating import templates

            info = await get_maintenance_info()
            return templates.TemplateResponse(
                "maintenance.html",
                {
                    "request": request,
                    "maintenance_active": True,
                    "maintenance_message": info.get("message", ""),
                    "vk_bot_url": getattr(settings, "vk_bot_url", "https://vk.com"),
                    "app_version": settings.APP_VERSION,
                },
                status_code=503,
                headers={"Retry-After": "300"},
            )

        return await call_next(request)
