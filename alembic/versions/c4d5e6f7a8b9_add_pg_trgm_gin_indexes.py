"""add pg_trgm extension and GIN trigram indexes for substring search

Revision ID: c4d5e6f7a8b9
Revises: b5a7c9d1e2f3
Create Date: 2026-09-14 00:00:00.000000

Почему нужно (Ошибка #9):
- Поиск по подстрокам (LIKE '%term%', ILIKE) в _build_ticket_search_filter
  не использует обычные B-tree индексы и сканирует всю таблицу tickets.
- Расширение pg_trgm разбивает текст на триграммы и позволяет GIN-индексам
  ускорять операции LIKE/ILIKE по подстрокам.
- GIN-индексы по topic, description, response_text ускоряют поиск.
- pg_trgm + GIN — только для PostgreSQL; на SQLite пропускается.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "b5a7c9d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# GIN-индексы для ускорения поиска по подстрокам (pg_trgm + gin_trgm_ops).
_TRGM_INDEXES = [
    ("ix_tickets_topic_trgm", "tickets", "topic"),
    ("ix_tickets_description_trgm", "tickets", "description"),
    ("ix_tickets_response_text_trgm", "tickets", "response_text"),
]


def upgrade() -> None:
    """Создаёт расширение pg_trgm и GIN-индексы для поиска по подстрокам."""
    bind = op.get_bind()
    # pg_trgm и GIN — расширения PostgreSQL; на SQLite пропускаем.
    if bind.dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for idx_name, table, column in _TRGM_INDEXES:
        op.execute(f"CREATE INDEX {idx_name} ON {table} USING gin ({column} gin_trgm_ops)")


def downgrade() -> None:
    """Удаляет GIN-индексы и расширение pg_trgm."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for idx_name, _table, _column in _TRGM_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {idx_name}")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
