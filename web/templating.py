from fastapi.templating import Jinja2Templates

# Вынесено в отдельный модуль, чтобы избежать циклического импорта:
# web.main импортирует роутеры, а роутерам нужен только templates.
templates = Jinja2Templates(directory="web/templates")

# Фильтры статусов заявки:
# {{ ticket.status|status_label }} — русская метка,
# {{ ticket.status|status_badge }} — CSS-класс бейджа.
from core.ticket_service import status_label  # noqa: E402
from web.constants import status_badge_class  # noqa: E402

templates.env.filters["status_label"] = status_label
templates.env.filters["status_badge"] = status_badge_class

from starlette.requests import Request  # noqa: E402
from starlette.responses import Response  # noqa: E402


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
