"""Маршруты раздела «Партнёрство» веб-панели (только для суперадминистратора)."""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from core.database import async_session_maker
from core.models import PartnershipRequest
from web.dependencies import require_superadmin
from web.security.csrf import get_csrf_token
from web.security.middleware import sanitize_html
from web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()

ALLOWED_STATUSES = {
    "new": "Новая",
    "in_progress": "В обработке",
    "contacted": "Связались",
    "rejected": "Отклонена",
}


@router.get("/")
async def partnerships_page(request: Request, user=Depends(require_superadmin)):
    """Страница заявок на партнёрство (доступна только суперадминистраторам)."""
    items: list[PartnershipRequest] = []
    db_error: bool = False

    try:
        async with async_session_maker() as session:
            stmt = select(PartnershipRequest).order_by(PartnershipRequest.created_at.desc())
            items = list((await session.scalars(stmt)).all())
    except Exception as e:
        db_error = True
        logger.error("Не удалось загрузить заявки на партнёрство: %s", e)

    return templates.TemplateResponse(
        "partnerships.html",
        {
            "request": request,
            "user": user,
            "items": items,
            "statuses": ALLOWED_STATUSES,
            "db_error": db_error,
            "active": "partnerships",
            "flash_success": request.session.pop("flash_success", None),
            "flash_error": request.session.pop("flash_error", None),
            "csrf_token": get_csrf_token(request),
            "session_id": getattr(request.state, "session_id", ""),
        },
    )


@router.post("/{request_id}/status")
async def update_partnership_status(
    request_id: int, request: Request, user=Depends(require_superadmin)
):
    """Обновление статуса заявки на партнёрство."""
    form = await request.form()
    new_status = sanitize_html(str(form.get("status", "")).strip())

    if new_status not in ALLOWED_STATUSES:
        request.session["flash_error"] = "Указан недопустимый статус"
        return RedirectResponse(url="/partnerships/", status_code=303)

    try:
        async with async_session_maker() as session:
            item = await session.get(PartnershipRequest, request_id)
            if item is None:
                request.session["flash_error"] = "Заявка на партнёрство не найдена"
                return RedirectResponse(url="/partnerships/", status_code=303)

            item.status = new_status
            await session.commit()
            request.session["flash_success"] = f"Статус заявки #{request_id} обновлён на «{ALLOWED_STATUSES[new_status]}»"
    except Exception:
        logger.exception("Ошибка при обновлении статуса заявки на партнёрство #%s", request_id)
        request.session["flash_error"] = "Не удалось обновить статус заявки"

    return RedirectResponse(url="/partnerships/", status_code=303)
