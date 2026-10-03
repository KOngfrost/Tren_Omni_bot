"""Глобальные HTTP-обработчики ошибок веб-панели."""

import logging
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.config import settings
from web.templating import templates

logger = logging.getLogger(__name__)


def _is_browser_request(request: Request) -> bool:
    """Проверить, является ли запрос браузерным (не API)."""
    return "application/json" not in request.headers.get("accept", "")


def _get_error_page_context(
    request: Request,
    status: int,
    error_id: str | None = None,
) -> dict:
    """Подготовить контекст шаблона error.html."""
    messages = {
        400: (
            "Неверный запрос",
            "Пожалуйста, проверьте введённые данные и попробуйте снова.",
            "",
            True,
            True,
            False,
        ),
        403: (
            "Доступ запрещён",
            "У вас нет прав для доступа к этой странице. Обратитесь к суперадминистратору.",
            "",
            True,
            True,
            False,
        ),
        404: (
            "Страница не найдена",
            "Запрошенная страница не существует или была перемещена.",
            "",
            True,
            True,
            True,
        ),
        405: (
            "Метод не разрешён",
            "Запрашиваемый метод HTTP не поддерживается для этой страницы.",
            "",
            True,
            True,
            False,
        ),
        413: (
            "Файл слишком большой",
            "Размер запроса превышает допустимый лимит. Попробуйте загрузить файл поменьше.",
            "",
            True,
            True,
            False,
        ),
        422: (
            "Некорректные данные",
            "Проверьте правильность заполнения формы и попробуйте снова.",
            "",
            True,
            True,
            False,
        ),
        429: (
            "Слишком много запросов",
            "Вы сделали слишком много запросов. Подождите минуту и попробуйте снова.",
            "",
            True,
            True,
            False,
        ),
        500: (
            "Внутренняя ошибка сервера",
            "Произошла ошибка. Пожалуйста, сфотографируйте экран и отправьте техническому администратору.",
            "",
            True,
            True,
            True,
        ),
    }
    if status in messages:
        title, message, icon, refresh, back, home = messages[status]
    else:
        title, message, icon, refresh, back, home = (
            f"Ошибка {status}",
            "Произошла ошибка. Пожалуйста, сфотографируйте экран и отправьте техническому администратору.",
            "",
            True,
            True,
            True,
        )
    context = {
        "request": request,
        "error_code": str(status),
        "error_title": title,
        "error_message": message,
        "error_icon": icon,
        "show_refresh": refresh,
        "show_back": back,
        "show_home": home,
    }
    if error_id:
        context["error_id"] = error_id
    return context


async def http_exception_handler(request: Request, exc: StarletteHTTPException | HTTPException):
    """HTML для браузерных HTTP ошибок, JSON для API и redirect для auth."""
    if exc.status_code in (302, 303) and exc.headers:
        return RedirectResponse(
            url=exc.headers.get("Location", "/auth/login"),
            status_code=exc.status_code,
            headers=exc.headers,
        )
    if _is_browser_request(request):
        return templates.TemplateResponse(
            "error.html",
            _get_error_page_context(request, exc.status_code),
            status_code=exc.status_code,
        )
    return JSONResponse(
        content={"detail": exc.detail},
        status_code=exc.status_code,
        headers=exc.headers,
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """422 ошибка валидации: для браузера — страница ошибки, для API — JSON."""
    if _is_browser_request(request):
        return templates.TemplateResponse(
            "error.html",
            _get_error_page_context(request, 422),
            status_code=422,
        )
    return JSONResponse(content={"detail": exc.errors()}, status_code=422)


async def global_exception_handler(request: Request, exc: Exception):
    """Скрыть production traceback и выдать request ID для корреляции."""
    request_id = uuid.uuid4().hex[:12]
    logger.exception(
        "Необработанное исключение на %s [request_id=%s]", request.url.path, request_id
    )

    is_api = (
        request.url.path.startswith("/tickets/")
        or request.url.path.startswith("/api/")
        or "application/json" in request.headers.get("accept", "")
    )
    if not settings.IS_PRODUCTION:
        if is_api:
            return JSONResponse(
                content={"detail": f"Ошибка: {type(exc).__name__}: {exc}", "traceback": str(exc)},
                status_code=500,
            )
        return HTMLResponse(content=f"<h1>Ошибка сервера</h1><pre>{exc}</pre>", status_code=500)

    if is_api:
        return JSONResponse(
            content={
                "detail": "Произошла ошибка. Пожалуйста, сфотографируйте экран и отправьте техническому администратору."
            },
            status_code=500,
            headers={"X-Request-ID": request_id},
        )
    return templates.TemplateResponse(
        "error.html",
        _get_error_page_context(request, 500, error_id=request_id),
        status_code=500,
        headers={"X-Request-ID": request_id},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Зарегистрировать HTTP, validation и fallback handlers на приложении."""
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)
