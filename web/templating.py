from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.responses import Response

from core.config import settings
from core.i18n import (
    current_locale,
    ngettext,
    pgettext,
    plural_index,
)
from core.i18n import (
    gettext as _,
)
from core.i18n import (
    ngettext as n_,
)
from core.ticket_service import status_label
from web.constants import status_badge_class
from web.security.csrf import get_csrf_token
from web.security.middleware import csp_nonce

# Вынесено в отдельный модуль, чтобы избежать циклического импорта:
# web.main импортирует роутеры, а роутерам нужен только templates.
templates = Jinja2Templates(directory="web/templates")

# Подключаем i18n-расширение для Jinja2
templates.env.add_extension("jinja2.ext.i18n")

# Регистрируем функции перевода в среде шаблонизатора
templates.env.globals["_"] = _
templates.env.globals["gettext"] = _
templates.env.globals["ngettext"] = ngettext
templates.env.globals["pgettext"] = pgettext
templates.env.globals["n_"] = n_
templates.env.globals["plural_index"] = plural_index
templates.env.globals["current_locale"] = current_locale

# Подключаем функции перевода для тегов {% trans %} Jinja2
templates.env.install_gettext_callables(
    gettext=_,
    ngettext=ngettext,
    pgettext=pgettext,
    newstyle=False,
)

# nonce для inline <script>/<style> (CSP). Функция, а не переменная контекста,
# потому что шаблоны рендерятся и вне HTTP-запроса (тесты, офлайн-генерация).
templates.env.globals["csp_nonce"] = csp_nonce
templates.env.globals["get_csrf_token"] = get_csrf_token


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

# Фильтр для плюрализации в шаблонах: {{ count|ticket_plural("заявка|заявки|заявок") }}
def ticket_plural(count: int, forms: str) -> str:
    """Выбрать правильную форму слова по количеству (для шаблонов)."""
    if not forms or not isinstance(count, int):
        return str(count)
    parts = forms.split("|")
    if len(parts) != 3:
        return str(count)
    idx = plural_index(count)
    return parts[idx]


templates.env.filters["ticket_plural"] = ticket_plural


def render_admin_template(
    request: Request,
    template_name: str,
    context: dict,
) -> Response:
    """Рендерить шаблон админки с автоматическим department_name и csrf_token из request."""
    # Устанавливаем локаль для текущего запроса
    locale_code = getattr(request.state, "locale", None) or current_locale()
    if locale_code:
        from core.i18n import set_current_locale

        set_current_locale(locale_code)

    csrf_val = context.get("csrf_token") or get_csrf_token(request)
    merged = {
        "request": request,
        "csrf_token": csrf_val,
        "department_name": getattr(request.state, "department_name", None),
        **context,
    }
    return templates.TemplateResponse(template_name, merged)
