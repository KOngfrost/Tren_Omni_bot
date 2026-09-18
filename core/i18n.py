"""Интернационализация (i18n): gettext + Babel.

Модуль даёт единую точку перевода для веб-панели (FastAPI/Jinja2) и VK-бота:

* ``_("Текст")`` — обычный перевод строки (gettext);
* ``pgettext("контекст", "Текст")`` — перевод с контекстом;
* ``ngettext(...)`` — стандартный gettext-эквивалент с плюрализацией;
* ``n_("%(n)s заявка|%(n)s заявки|%(n)s заявок", 5)`` — счётчики с корректными
  русскими формами (1 заявка / 2 заявки / 5 заявок).

Текущая локаль хранится в ``contextvars.ContextVar``, поэтому параллельные
запросы в одном event loop не перезаписывают локаль друг друга. Значение по
умолчанию берётся из настроек приложения (``DEFAULT_LOCALE``), а каталоги
gettext подгружаются лениво и кэшируются из ``translations/<locale>/LC_MESSAGES``.

Если ``.mo``-каталог не скомпилирован, перевод корректно деградирует до msgid
(исходный русский текст), а выбор формы счётчика выполняется по встроенным
правилам языка — счётчики остаются корректными без пайплайна Babel.
"""

from __future__ import annotations

import contextlib
import gettext as gettext_module
import logging
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Формы одного msgid разделяются символом "|": "заявка|заявки|заявок".
PLURAL_FORM_SEPARATOR = "|"

# Имя параметра, доступного внутри строк с плюрализацией: "%(n)s заявок".
PLURAL_COUNT_KEY = "n"

# Запасные правила плюрализации (индекс формы), когда каталог .mo не загружен.
# Порядок форм в msgid совпадает с порядком форм в gettext (nplurals).
_FALLBACK_PLURAL_RULES: dict[str, Any] = {
    "ru": lambda n: (
        0
        if n % 10 == 1 and n % 100 != 11
        else 1
        if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14)
        else 2
    ),
    "uk": lambda n: (
        0
        if n % 10 == 1 and n % 100 != 11
        else 1
        if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14)
        else 2
    ),
    "en": lambda n: 0 if n == 1 else 1,
}
_DEFAULT_PLURAL_RULE = lambda n: 0 if n == 1 else 1  # noqa: E731

# Локаль по умолчанию, если не задана в настройках
_HARD_DEFAULT_LOCALE = "ru"

# Текущая локаль запроса/задачи. Пустая строка — «не задана», берётся из настроек.
_current_locale: ContextVar[str] = ContextVar("i18n_locale", default="")

_ACCEPT_LANGUAGE_ITEM = re.compile(r"^\s*(?P<tag>[A-Za-z0-9-]+)\s*(?:;\s*q\s*=\s*(?P<q>[\d.]+))?\s*$")


def _settings() -> Any:
    """Настройки приложения (ленивый импорт — защита от циклических импортов)."""
    from core.config import settings

    return settings


def _primary_tag(value: Any) -> str | None:
    """Основной подтег языка: ``ru-RU``/``ru_RU.UTF-8`` → ``ru``."""
    if not value:
        return None
    tag = str(value).strip().replace("_", "-").split(".", 1)[0]
    if not tag:
        return None
    return tag.split("-", 1)[0].lower()


def default_locale() -> str:
    """Локаль по умолчанию из настроек (ru, если значение не задано)."""
    return _primary_tag(getattr(_settings(), "DEFAULT_LOCALE", None)) or _HARD_DEFAULT_LOCALE


def supported_locales() -> tuple[str, ...]:
    """Список включённых локалей (всегда содержит локаль по умолчанию)."""
    raw = getattr(_settings(), "SUPPORTED_LOCALES", None) or []
    codes: list[str] = []
    if isinstance(raw, str):
        raw = raw.split(",")
    for item in raw:
        code = _primary_tag(item)
        if code and code not in codes:
            codes.append(code)
    fallback = default_locale()
    if fallback not in codes:
        codes.insert(0, fallback)
    return tuple(codes)


def translations_dir() -> Path:
    """Каталог с gettext-каталогами проекта (``translations/`` в корне)."""
    override = getattr(_settings(), "TRANSLATIONS_DIR", None)
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "translations"


def normalize_locale(value: str | None) -> str | None:
    """Нормализовать тег языка: ``ru-RU``/``ru_RU.UTF-8`` → ``ru``.

    Возвращает ``None``, если тег не входит в список поддерживаемых.
    """
    if not value:
        return None
    tag = str(value).strip().replace("_", "-").split(".", 1)[0].lower()
    if not tag:
        return None
    supported = supported_locales()
    if tag in supported:
        return tag
    primary = tag.split("-", 1)[0]
    return primary if primary in supported else None


@lru_cache(maxsize=32)
def load_catalog(locale: str) -> gettext_module.NullTranslations:
    """Загрузить (и закэшировать) gettext-каталог локали.

    При отсутствии скомпилированного ``.mo`` возвращается NullTranslations —
    перевод деградирует до исходного msgid, а не падает.
    """
    code = normalize_locale(locale) or default_locale()
    domain = getattr(_settings(), "TRANSLATION_DOMAIN", "messages")
    fallback = gettext_module.NullTranslations()
    try:
        catalog = gettext_module.translation(
            domain=domain,
            languages=[code],
            localedir=str(translations_dir()),
            fallback=None,
        )
    except (OSError, Exception) as exc:
        logger.warning("Не удалось загрузить каталог локализации для '%s': %s", code, exc)
        return fallback
    if isinstance(catalog, gettext_module.NullTranslations):
        return fallback
    catalog.add_fallback(fallback)
    return catalog


def set_locale(locale: str | None) -> str:
    """Установить текущую локаль; вернуть фактически применённый код."""
    code = normalize_locale(locale) or default_locale()
    _current_locale.set(code)
    return code


def current_locale() -> str:
    """Текущая локаль (если не задана — локаль по умолчанию)."""
    return _current_locale.get() or default_locale()


@contextmanager
def use_locale(locale: str | None) -> Iterator[str]:
    """Временно переключить локаль (для бота, фоновых задач и тестов)."""
    token = _current_locale.set(normalize_locale(locale) or default_locale())
    try:
        yield _current_locale.get()
    finally:
        _current_locale.reset(token)


def gettext(message: str) -> str:
    """Перевести строку на текущий язык."""
    if not message:
        return message
    return load_catalog(current_locale()).gettext(message)


def pgettext(context: str, message: str) -> str:
    """Перевести строку с контекстом (одинаковый msgid в разных смыслах)."""
    if not message:
        return message
    return load_catalog(current_locale()).pgettext(context, message)


def ngettext(singular: str, plural: str, count: int) -> str:
    """Стандартный gettext-перевод с плюрализацией (nplurals из каталога)."""
    return load_catalog(current_locale()).ngettext(singular, plural, int(count))


def _plural_rule(locale: str) -> Any:
    """Функция выбора формы: из заголовка Plural-Forms каталога либо из таблицы."""
    catalog = load_catalog(locale)
    plural = getattr(catalog, "plural", None)
    if callable(plural) and not isinstance(catalog, gettext_module.NullTranslations):
        return plural
    return _FALLBACK_PLURAL_RULES.get(locale, _DEFAULT_PLURAL_RULE)


def n_(forms: str, count: int, **kwargs: Any) -> str:
    """Фраза со счётчиком и корректной формой слова для текущего языка.

    ``forms`` — формы через ``|`` в порядке gettext-каталога, например
    ``"%(n)s заявка|%(n)s заявки|%(n)s заявок"``. Форматирование выполняет
    ``%(n)s`` (доступен также ``{n}`` для шаблонов без gettext-разметки).
    """
    if not forms:
        return forms
    catalog = load_catalog(current_locale())
    translated = catalog.gettext(forms) if catalog else forms
    parts = [part.strip() for part in translated.split(PLURAL_FORM_SEPARATOR)]
    if len(parts) == 1:
        template = parts[0]
    else:
        index = int(_plural_rule(current_locale())(int(count)))
        template = parts[min(index, len(parts) - 1)]
    params = {PLURAL_COUNT_KEY: int(count), **kwargs}
    if "%(" in template:
        with contextlib.suppress(KeyError, ValueError, TypeError):
            template = template % params
    elif "{" in template:
        with contextlib.suppress(KeyError, IndexError, ValueError):
            template = template.format(**params)
    return template


def plural_index(count: int, locale: str | None = None) -> int:
    """Индекс формы плюрализации для ``count`` (нужен в тестах и шаблонах)."""
    code = normalize_locale(locale) or current_locale()
    return int(_plural_rule(code)(int(count)))


def negotiate_locale(
    accept_language: str | None,
    available: Sequence[str] | None = None,
) -> str | None:
    """Согласовать язык из заголовка ``Accept-Language`` с учётом q-значений."""
    if not accept_language:
        return None
    pool = list(available) if available else list(supported_locales())
    items: list[tuple[float, int, str]] = []
    for position, raw_item in enumerate(accept_language.split(",")):
        match = _ACCEPT_LANGUAGE_ITEM.match(raw_item)
        if not match:
            continue
        tag = match.group("tag")
        if tag == "*":
            continue
        try:
            quality = float(match.group("q")) if match.group("q") else 1.0
        except ValueError:
            quality = 0.0
        code = normalize_locale(tag)
        if code is None and tag.split("-", 1)[0].lower() in {"ru", "en", "uk"}:
            code = tag.split("-", 1)[0].lower()
        if code and code in pool:
            # Сортировка: больший q раньше, при равенстве — порядок в заголовке.
            items.append((quality, -position, code))
    if not items:
        return None
    items.sort(reverse=True)
    return items[0][2]


def parse_accept_language(accept_language: str | None) -> list[str]:
    """Развернуть ``Accept-Language`` в упорядоченный список тегов языка."""
    if not accept_language:
        return []
    items: list[tuple[float, int, str]] = []
    for position, raw_item in enumerate(accept_language.split(",")):
        match = _ACCEPT_LANGUAGE_ITEM.match(raw_item)
        if not match or match.group("tag") == "*":
            continue
        try:
            quality = float(match.group("q")) if match.group("q") else 1.0
        except ValueError:
            continue
        items.append((quality, -position, match.group("tag")))
    items.sort(reverse=True)
    return [tag for _q, _pos, tag in items]


def format_number(value: int | float, locale: str | None = None) -> str:
    """Локализованное форматирование числа (разделители тысяч и дробной части)."""
    code = normalize_locale(locale) or current_locale()
    try:
        from babel.numbers import format_decimal

        return format_decimal(value, locale=code)
    except Exception:  # pragma: no cover - babel обязателен, но не блокируем UI
        return str(value)


def format_datetime(
    value: Any,
    format: str = "short",
    locale: str | None = None,
) -> str:
    """Локализованное форматирование даты/времени через Babel."""
    code = normalize_locale(locale) or current_locale()
    try:
        from babel.dates import format_datetime as _format_datetime

        return _format_datetime(value, format=format, locale=code)
    except Exception:  # pragma: no cover - не роняем страницу из-за локали
        return str(value)


def locale_display_name(locale: str | None = None) -> str:
    """Название языка на его собственном языке (для переключателя в UI)."""
    code = normalize_locale(locale) or current_locale()
    try:
        from babel import Locale

        return Locale.parse(code).get_display_name(code) or code
    except Exception:  # pragma: no cover
        return code


def available_locales() -> list[tuple[str, str]]:
    """Пары ``(код, название)`` для переключателя языка."""
    return [(code, locale_display_name(code)) for code in supported_locales()]


def is_supported(locale: str | None) -> bool:
    """Проверка вхождения тега в поддерживаемые локали."""
    return normalize_locale(locale) is not None


def clear_cache() -> None:
    """Сбросить кэш каталогов (после pybabel compile в том же процессе)."""
    load_catalog.cache_clear()
