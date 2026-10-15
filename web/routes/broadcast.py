"""Маршруты массовой рассылки сообщений пользователям (Broadcast).

Безопасность:
- Доступно ИСКЛЮЧИТЕЛЬНО суперадминистраторам (SUPERADMIN).
- CSRF защищён middleware CSRFMiddleware в main.py.
- Санитизация HTML-разметки от XSS (sanitize_html).
- Идемпотентность и защита от повторной отправки.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from core.database import async_session_maker
from core.services.broadcast_service import (
    enqueue_broadcast,
    get_recent_users,
    get_total_users_count,
)
from core.time_utils import format_app_datetime
from web.dependencies import can_access_broadcast, require_auth, require_writer
from web.form_utils import parse_form_int
from web.security.csrf import get_csrf_token
from web.security.middleware import sanitize_html
from web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()

BROADCAST_FORBIDDEN_MSG = "У вас нет прав для доступа к массовой рассылке."


@router.get("/")
async def broadcast_page(request: Request, user: dict = Depends(require_auth)):
    """Страница создания рассылки и предпросмотра получателей."""
    if not can_access_broadcast(user):
        request.session["flash_error"] = BROADCAST_FORBIDDEN_MSG
        return RedirectResponse(url="/", status_code=303)

    async with async_session_maker() as session:
        total_users = await get_total_users_count(session)
        # По умолчанию показываем первых 10 или сколько есть
        default_count = min(10, total_users) if total_users > 0 else 1
        recent_users = await get_recent_users(session, limit=default_count)

    return templates.TemplateResponse(
        "broadcast.html",
        {
            "request": request,
            "user": user,
            "total_users": total_users,
            "recent_users": recent_users,
            "preview_count": default_count,
            "active": "broadcast",
            "flash_success": request.session.pop("flash_success", None),
            "flash_error": request.session.pop("flash_error", None),
            "csrf_token": get_csrf_token(request),
            "session_id": request.state.session_id,
        },
    )


@router.get("/preview")
async def broadcast_preview(
    request: Request,
    count: int = 10,
    user: dict = Depends(require_auth),
):
    """AJAX-эндпоинт динамического предпросмотра N последних получателей."""
    if not can_access_broadcast(user):
        return JSONResponse(status_code=403, content={"error": "Доступ запрещен"})

    async with async_session_maker() as session:
        limit = min(max(count, 1), 100)
        users = await get_recent_users(session, limit=limit)
        return JSONResponse(
            {
                "count": len(users),
                "users": [
                    {
                        "id": u.id,
                        "vk_id": u.vk_id,
                        "full_name": u.full_name or "Не указано",
                        "dormitory": u.dormitory or "—",
                        "last_seen": format_app_datetime(u.last_seen_at or u.created_at),
                    }
                    for u in users
                ],
            }
        )


@router.post("/send")
async def broadcast_send(request: Request, user: dict = Depends(require_writer)):
    """Обработка отправки рассылки N последним пользователям."""
    if not can_access_broadcast(user):
        request.session["flash_error"] = BROADCAST_FORBIDDEN_MSG
        return RedirectResponse(url="/", status_code=303)

    async with async_session_maker() as session:
        form = await request.form()
        parsed_count = parse_form_int(form, "count", default=0)
        count = parsed_count if parsed_count is not None else 0
        raw_text = str(form.get("text", "")).strip()
        text = sanitize_html(raw_text)

        if count <= 0:
            request.session["flash_error"] = (
                "Укажите корректное положительное число пользователей для отправки."
            )
            return RedirectResponse(url="/broadcast/", status_code=303)

        if not text:
            request.session["flash_error"] = "Текст рассылки не может быть пустым."
            return RedirectResponse(url="/broadcast/", status_code=303)

        result = await enqueue_broadcast(
            session=session,
            text=text,
            count=count,
            author_name=str(user.get("username", "superadmin")),
            actor_type="web",
        )

        if result.status == "success":
            request.session["flash_success"] = (
                f"Рассылка успешно сформирована! Поставлено в очередь отправки: {result.recipients_count} пользователей."
            )
        else:
            request.session["flash_error"] = result.error or "Не удалось отправить рассылку."

    return RedirectResponse(url="/broadcast/", status_code=303)
