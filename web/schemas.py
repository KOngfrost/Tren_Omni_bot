"""Pydantic-схемы контрактов JSON API и веб-форм.

Зачем: тела запросов раньше разбирались вручную (``body.get("name", "")``),
из-за чего невалидный payload приводил к AttributeError/TypeError и ответу
500 вместо 400. Pydantic-схема валидирует контракт автоматически:

- JSON-эндпоинты (web/routes/api.py) валидируют тело через
  ``Schema.model_validate(await request.json())``; любая ошибка валидации
  даёт 400 с человекочитаемым сообщением — контракт фронта
  ``{success: false, error: str}`` сохраняется;
- HTML-формы используют те же схемы, чтобы правила (обязательность полей,
  лимиты длины) не дублировались между JSON API и формами.

Новые эндпоинты обязаны объявлять схему запроса здесь.
"""

from typing import Any

from pydantic import BaseModel, field_validator

# Максимальная длина названия отдела — общий лимит JSON API и HTML-форм.
MAX_DEPARTMENT_NAME_LEN = 80


def coerce_optional_str(value: Any) -> str:
    """Привести значение к строке без TypeError/AttributeError.

    - ``None`` → ``""`` (отсутствующее поле, а не строка "None");
    - строки возвращаются как есть;
    - прочие скаляры приводятся ``str()`` — как прежний ручной разбор.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


class DepartmentNamePayload(BaseModel):
    """Тело запросов создания/переименования отдела: ``{"name": "..."}``.

    Используется и JSON API (web/routes/api.py), и HTML-формами
    (web/routes/departments.py), чтобы лимит длины и приведение типов
    были заданы в одном месте.
    """

    name: str = ""

    @field_validator("name", mode="before")
    @classmethod
    def _name_to_str(cls, value: Any) -> str:
        return coerce_optional_str(value)
