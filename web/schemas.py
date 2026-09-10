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


class DepartmentUsageSchema(BaseModel):
    """Статистика использования отдела."""

    tickets: int = 0
    knowledge: int = 0
    faq: int = 0
    events: int = 0
    web_users: int = 0
    subscriptions: int = 0


class DepartmentSchema(BaseModel):
    """Схема данных отдела в ответах API v1."""

    id: int
    name: str
    created_at: str | None = None
    usage: DepartmentUsageSchema | None = None


class SystemStatsResponse(BaseModel):
    """Схема статистики системы для API v1."""

    version: str
    environment: str
    redis_connected: bool
    sentry_enabled: bool
    two_factor_enabled: bool
    total_tickets: int = 0
    active_tickets: int = 0


class TicketSummarySchema(BaseModel):
    """Схема краткой информации о заявке в API v1."""

    id: int
    topic: str
    status: str
    department_id: int | None = None
    department_name: str | None = None
    created_at: str | None = None
