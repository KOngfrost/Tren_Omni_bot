"""add explicit ON DELETE behavior to foreign keys

Revision ID: b5a7c9d1e2f3
Revises: e8f9a0b1c2d3
Create Date: 2026-09-13 12:00:00.000000

Проблема (Ошибка #13): модели SQLAlchemy объявляют ``ondelete``
(CASCADE / SET NULL), но в БД внешние ключи созданы миграцией
a1b2c3d4e5f6 БЕЗ поведения при удалении родительской строки.
Поэтому удаление пользователя / отдела / события завершается
необработанным IntegrityError (поведение RESTRICT по умолчанию).

Решение: пересоздаём FK с явным ``ON DELETE``:
- CASCADE: admins.user_id, registrations.user_id, registrations.event_id,
  events.department_id, subscriptions.user_id, subscriptions.department_id,
  knowledge_base.department_id, faq_nodes.department_id;
- SET NULL: tickets.user_id, tickets.department_id, admins.department_id,
  faq_nodes.parent_id, logs.user_id.

Имена новых ограничений фиксируем явно
(``fk_<table>_<column>_<behavior>``), не полагаясь на дефолтные имена
PostgreSQL. Upgrade идемпотентен: перед изменением проверяем существование
ограничений через inspector.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5a7c9d1e2f3"
down_revision: str | Sequence[str] | None = "e8f9a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, old_default_name, new_name, columns, ref_table, ref_columns, ondelete)
_FOREIGN_KEY_SPECS = [
    # Требования Ошибки #13
    (
        "admins",
        "admins_user_id_fkey",
        "fk_admins_user_id_cascade",
        ["user_id"],
        "users",
        ["id"],
        "CASCADE",
    ),
    (
        "registrations",
        "registrations_user_id_fkey",
        "fk_registrations_user_id_cascade",
        ["user_id"],
        "users",
        ["id"],
        "CASCADE",
    ),
    (
        "registrations",
        "registrations_event_id_fkey",
        "fk_registrations_event_id_cascade",
        ["event_id"],
        "events",
        ["id"],
        "CASCADE",
    ),
    (
        "tickets",
        "tickets_user_id_fkey",
        "fk_tickets_user_id_set_null",
        ["user_id"],
        "users",
        ["id"],
        "SET NULL",
    ),
    (
        "tickets",
        "tickets_department_id_fkey",
        "fk_tickets_department_id_set_null",
        ["department_id"],
        "departments",
        ["id"],
        "SET NULL",
    ),
    # Остальные FK моделей — для полного устранения IntegrityError
    (
        "admins",
        "admins_department_id_fkey",
        "fk_admins_department_id_set_null",
        ["department_id"],
        "departments",
        ["id"],
        "SET NULL",
    ),
    (
        "events",
        "events_department_id_fkey",
        "fk_events_department_id_cascade",
        ["department_id"],
        "departments",
        ["id"],
        "CASCADE",
    ),
    (
        "subscriptions",
        "subscriptions_user_id_fkey",
        "fk_subscriptions_user_id_cascade",
        ["user_id"],
        "users",
        ["id"],
        "CASCADE",
    ),
    (
        "subscriptions",
        "subscriptions_department_id_fkey",
        "fk_subscriptions_department_id_cascade",
        ["department_id"],
        "departments",
        ["id"],
        "CASCADE",
    ),
    (
        "knowledge_base",
        "knowledge_base_department_id_fkey",
        "fk_knowledge_base_department_id_cascade",
        ["department_id"],
        "departments",
        ["id"],
        "CASCADE",
    ),
    (
        "faq_nodes",
        "faq_nodes_department_id_fkey",
        "fk_faq_nodes_department_id_cascade",
        ["department_id"],
        "departments",
        ["id"],
        "CASCADE",
    ),
    (
        "faq_nodes",
        "faq_nodes_parent_id_fkey",
        "fk_faq_nodes_parent_id_set_null",
        ["parent_id"],
        "faq_nodes",
        ["id"],
        "SET NULL",
    ),
    (
        "logs",
        "logs_user_id_fkey",
        "fk_logs_user_id_set_null",
        ["user_id"],
        "users",
        ["id"],
        "SET NULL",
    ),
]


def _fk_names(bind, table: str) -> set[str]:
    """Имена внешних ключей таблицы через inspector (без падения на отсутствующей)."""
    try:
        return {fk["name"] for fk in sa.inspect(bind).get_foreign_keys(table)}
    except Exception:
        return set()


def _recreate_fk(
    spec: tuple[str, str, str, list[str], str, list[str], str], *, downgrade: bool = False
) -> None:
    """Заменить FK. При upgrade: drop бездействующего -> add с ON DELETE.

    При downgrade — обратная операция: drop именованного -> add исходного
    (без поведения, как в начальной схеме).
    """
    table, old_name, new_name, columns, ref_table, ref_columns, ondelete = spec
    bind = op.get_bind()
    names = _fk_names(bind, table)
    if not downgrade:
        if old_name in names:
            op.drop_constraint(old_name, table, type_="foreignkey")
        if new_name in names:
            return  # уже пересоздан (повторный запуск)
        op.create_foreign_key(new_name, table, ref_table, columns, ref_columns, ondelete=ondelete)
    else:
        if new_name in names:
            op.drop_constraint(new_name, table, type_="foreignkey")
        if old_name in names:
            return  # уже восстановлен
        op.create_foreign_key(old_name, table, ref_table, columns, ref_columns)


def upgrade() -> None:
    """Пересоздать внешние ключи с явным поведением ON DELETE."""
    for spec in _FOREIGN_KEY_SPECS:
        _recreate_fk(spec)


def downgrade() -> None:
    """Восстановить ограничения без ON DELETE (поведение начальной схемы)."""
    for spec in _FOREIGN_KEY_SPECS:
        _recreate_fk(spec, downgrade=True)
