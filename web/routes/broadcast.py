"""Маршруты массовой рассылки сообщений пользователям (Broadcast).

Безопасность:
- Доступно ИСКЛЮЧИТЕЛЬНО суперадминистраторам (SUPERADMIN).
- CSRF защищён middleware CSRFMiddleware в main.py.
- Санитизация HTML-разметки от XSS (sanitize_html).
- Идемпотентность и защита от повторной отправки.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.datastructures import UploadFile

from core.database import async_session_maker
from core.services.broadcast_service import (
    enqueue_broadcast,
    get_recent_users,
    get_total_users_count,
    parse_vk_post_reference,
)
from core.time_utils import format_app_datetime
from core.vk_upload import upload_photo_for_messages
from web.dependencies import can_access_broadcast, require_auth, require_writer
from web.form_utils import parse_form_int
from web.security.csrf import get_csrf_token
from web.security.middleware import sanitize_html
from web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()

BROADCAST_FORBIDDEN_MSG = "У вас нет прав для доступа к массовой рассылке."


def _parse_excluded_ids(raw_excluded: str) -> list[int] | None:
    """Извлечь список ID пользователей для исключения из рассылки."""
    if not raw_excluded:
        return None
    parsed_ids: list[int] = []
    for item in raw_excluded.replace(";", ",").split(","):
        item_str = item.strip()
        if item_str.isdigit():
            parsed_ids.append(int(item_str))
    return parsed_ids or None


def _parse_post_attachment(raw_post_url: str) -> tuple[str | None, str | None]:
    """Распознать пост ВКонтакте и вернуть (attachment, error_msg)."""
    if not raw_post_url:
        return None, None
    attachment = parse_vk_post_reference(raw_post_url)
    if not attachment:
        return (
            None,
            "Некорректная ссылка на пост ВКонтакте. Поддерживаются ссылки формата vk.com/wall-XXX_YYY.",
        )
    return attachment, None


MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB limit to prevent OOM DoS


async def _process_image_attachment(image_field: Any) -> tuple[str | None, str | None]:
    """Загрузить изображение в ВК и вернуть (attachment, error_msg) с валидацией размера."""
    if not isinstance(image_field, UploadFile) or not image_field.filename:
        return None, None
    # Читаем не более MAX_IMAGE_SIZE_BYTES + 1 для защиты от исчерпания памяти (DoS)
    content = await image_field.read(MAX_IMAGE_SIZE_BYTES + 1)
    if not content:
        return None, None
    if len(content) > MAX_IMAGE_SIZE_BYTES:
        return None, "Размер изображения превышает максимально допустимый (10 МБ)."
    try:
        attachment = await upload_photo_for_messages(content, filename=image_field.filename)
        return attachment, None
    except Exception as exc:
        logger.error("Ошибка загрузки изображения для рассылки: %s", exc)
        return None, f"Ошибка загрузки изображения: {exc}"


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
    """Обработка отправки рассылки N последним пользователям с вложениями и исключением."""
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

        excluded_user_ids = _parse_excluded_ids(str(form.get("excluded_ids", "")).strip())

        post_attachment, post_err = _parse_post_attachment(str(form.get("post_url", "")).strip())
        if post_err:
            request.session["flash_error"] = post_err
            return RedirectResponse(url="/broadcast/", status_code=303)

        photo_attachment, photo_err = await _process_image_attachment(form.get("image"))
        if photo_err:
            request.session["flash_error"] = photo_err
            return RedirectResponse(url="/broadcast/", status_code=303)

        attachments = [att for att in (post_attachment, photo_attachment) if att]
        attachment_str = ",".join(attachments) if attachments else None

        # Валидация наличия контента (текст ИЛИ вложение)
        if not text and not attachment_str:
            request.session["flash_error"] = (
                "Укажите текст сообщения или прикрепите пост/изображение."
            )
            return RedirectResponse(url="/broadcast/", status_code=303)

        result = await enqueue_broadcast(
            session=session,
            text=text,
            count=count,
            excluded_user_ids=excluded_user_ids,
            attachment=attachment_str,
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
