"""Тесты для модуля интернационализации (core/i18n.py)."""

from core.i18n import (
    current_locale,
    default_locale,
    gettext,
    n_,
    ngettext,
    normalize_locale,
    pgettext,
    plural_index,
    set_locale,
    supported_locales,
    use_locale,
)


def test_default_locale():
    """Проверка локали по умолчанию."""
    assert default_locale() == "ru"
    assert current_locale() == "ru"


def test_supported_locales():
    """Проверка списка поддерживаемых локалей."""
    locales = supported_locales()
    assert "ru" in locales


def test_normalize_locale():
    """Проверка нормализации тегов языков."""
    assert normalize_locale("ru_RU.UTF-8") == "ru"
    assert normalize_locale("ru-RU") == "ru"
    assert normalize_locale("invalid_xyz") is None
    assert normalize_locale("") is None


def test_set_and_get_locale():
    """Смена локали через set_locale."""
    set_locale("ru")
    assert current_locale() == "ru"


def test_use_locale_context_manager():
    """Контекстный менеджер use_locale изолирует изменения локали."""
    set_locale("ru")
    with use_locale("ru"):
        assert current_locale() == "ru"
    assert current_locale() == "ru"


def test_gettext_fallback():
    """При отсутствии перевода возвращается исходный msgid."""
    with use_locale("ru"):
        assert gettext("Привет, мир!") == "Привет, мир!"
        assert gettext("") == ""


def test_pgettext_fallback():
    """pgettext возвращает msgid при отсутствии перевода."""
    with use_locale("ru"):
        assert pgettext("Кнопка", "Сохранить") == "Сохранить"
        assert pgettext("Кнопка", "") == ""


def test_plural_index_russian():
    """Проверка индексов форм для русского языка (1 -> 0, 2-4 -> 1, 5-20 -> 2)."""
    assert plural_index(1, "ru") == 0
    assert plural_index(2, "ru") == 1
    assert plural_index(4, "ru") == 1
    assert plural_index(5, "ru") == 2
    assert plural_index(11, "ru") == 2
    assert plural_index(21, "ru") == 0
    assert plural_index(22, "ru") == 1


def test_n_plural_russian():
    """Проверка форматирования строк со счётчиками для русского языка."""
    template = "%(n)s заявка|%(n)s заявки|%(n)s заявок"
    with use_locale("ru"):
        assert n_(template, 1) == "1 заявка"
        assert n_(template, 2) == "2 заявки"
        assert n_(template, 4) == "4 заявки"
        assert n_(template, 5) == "5 заявок"
        assert n_(template, 11) == "11 заявок"
        assert n_(template, 14) == "14 заявок"
        assert n_(template, 21) == "21 заявка"
        assert n_(template, 22) == "22 заявки"
        assert n_(template, 25) == "25 заявок"
        assert n_(template, 100) == "100 заявок"
        assert n_(template, 101) == "101 заявка"


def test_ngettext_fallback():
    """Стандартный ngettext fallback."""
    with use_locale("ru"):
        assert ngettext("заявка", "заявки", 1) == "заявка"
        assert ngettext("заявка", "заявки", 2) == "заявки"
