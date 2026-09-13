from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.responses import Response

from core.config import settings
from core.ticket_service import status_label
from web.constants import status_badge_class

# Вынесено в отдельный модуль, чтобы избежать циклического импорта:
# web.main импортирует роутеры, а роутерам нужен только templates.
templates = Jinja2Templates(directory="web/templates")


def format_datetime(dt: datetime | None, fmt: str = "%d.%m.%Y %H:%M") -> str:
    """Форматирует дату/время с автоматическим переводом в часовой пояс приложения (Europe/Moscow)."""
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    try:
        app_tz = ZoneInfo(settings.APP_TIMEZONE)
        return dt.astimezone(app_tz).strftime(fmt)
    except Exception:
        return dt.strftime(fmt)


templates.env.filters["status_label"] = status_label
templates.env.filters["status_badge"] = status_badge_class
templates.env.filters["format_dt"] = format_datetime


def render_admin_template(
    request: Request,
    template_name: str,
    context: dict,
) -> Response:
    """Рендерить шаблон админки с автоматическим department_name из request.state."""
    merged = {
        "request": request,
        "department_name": getattr(request.state, "department_name", None),
        **context,
    }
    return templates.TemplateResponse(template_name, merged)
