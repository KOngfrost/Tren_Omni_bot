"""Публичные маршруты для правовых документов и соблюдения законодательства РФ (152-ФЗ).

Страницы доступны без обязательной авторизации:
- /legal/privacy — Политика обработки персональных данных (ст. 18.1 152-ФЗ)
- /legal/consent — Согласие на обработку персональных данных (ст. 9 152-ФЗ)
- /legal/terms — Пользовательское соглашение и правила пользования сервисом
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from web.security.csrf import get_csrf_token
from web.templating import templates

router = APIRouter()


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_policy_page(request: Request) -> HTMLResponse:
    """Страница политики обработки персональных данных (152-ФЗ)."""
    user = request.session.get("user")
    return templates.TemplateResponse(
        "legal/privacy.html",
        {
            "request": request,
            "user": user,
            "csrf_token": get_csrf_token(request),
            "canonical_path": "/legal/privacy",
        },
    )


@router.get("/consent", response_class=HTMLResponse)
async def consent_page(request: Request) -> HTMLResponse:
    """Страница согласия на обработку персональных данных (ст. 9 152-ФЗ)."""
    user = request.session.get("user")
    return templates.TemplateResponse(
        "legal/consent.html",
        {
            "request": request,
            "user": user,
            "csrf_token": get_csrf_token(request),
            "canonical_path": "/legal/consent",
        },
    )


@router.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request) -> HTMLResponse:
    """Страница пользовательского соглашения и правил сервиса."""
    user = request.session.get("user")
    return templates.TemplateResponse(
        "legal/terms.html",
        {
            "request": request,
            "user": user,
            "csrf_token": get_csrf_token(request),
            "canonical_path": "/legal/terms",
        },
    )
