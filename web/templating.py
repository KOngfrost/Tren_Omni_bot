from collections.abc import Callable
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


def _nonce_for_request(request) -> Callable[[str], str]:
    """Функция csp_nonce(...), привязанная к конкретному запросу.

    Приоритет: request.state (надёжно — задаётся SecurityHeadersMiddleware
    в scope и виден всем роутам) → глобальный ContextVar (fallback для
    офлайн-рендера и тестов, где middleware не выполнялся).
    """

    def _csp_nonce(kind: str = "script") -> str:
        if request is not None:
            state = getattr(request, "state", None)
            if state is not None:
                stored = getattr(state, "csp_nonce", None)
                if isinstance(stored, dict):
                    value = stored.get(kind)
                    if value:
                        return value
                value = getattr(state, f"{kind}_nonce", None)
                if value:
                    return value
        return csp_nonce(kind)

    return _csp_nonce


class _RequestAwareTemplates(Jinja2Templates):
    """Jinja2Templates, гарантирующий csp_nonce в контексте каждого ответа.

    Любой вызов TemplateResponse (все роуты, error-хендлеры, maintenance)
    получает в контекст функцию ``csp_nonce('script'|'style')``, которая
    читает nonce текущего запроса из request.state. Это устраняет блокировку
    inline-скриптов браузером при CSP с nonce, когда ContextVar неактуален.
    """

    def TemplateResponse(self, *args, **kwargs):  # API Starlette
        request, context = self._extract_request_and_context(args, kwargs)
        if context is not None and "csp_nonce" not in context:
            context["csp_nonce"] = _nonce_for_request(request)
        return super().TemplateResponse(*args, **kwargs)

    @staticmethod
    def _extract_request_and_context(args, kwargs):
        """Нормализовать оба стиля вызова Starlette (старый и новый).

        Старый: TemplateResponse(name, context) — request лежит в context.
        Новый:  TemplateResponse(request, name, context).
        """
        request = None
        context = None
        if args:
            if isinstance(args[0], str):
                # Старый стиль: первый аргумент — имя шаблона
                context = args[1] if len(args) > 1 else kwargs.get("context")
                if isinstance(context, dict):
                    request = context.get("request")
            else:
                # Новый стиль: первый аргумент — Request
                request = args[0]
                context = args[2] if len(args) > 2 else kwargs.get("context")
        else:
            context = kwargs.get("context")
            request = kwargs.get("request")
            if request is None and isinstance(context, dict):
                request = context.get("request")
        if not isinstance(context, dict):
            context = None
        return request, context


templates = _RequestAwareTemplates(directory="web/templates")

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
templates.env.globals["app_version"] = settings.APP_VERSION
templates.env.globals["project_version"] = settings.APP_VERSION
templates.env.globals["css_version"] = f"{settings.APP_VERSION}.2"


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
        "app_version": settings.APP_VERSION,
        "project_version": settings.APP_VERSION,
        **context,
    }
    return templates.TemplateResponse(template_name, merged)
