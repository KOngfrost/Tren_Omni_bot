"""Add per-user 2FA flag and full web-panel audit columns.

Revision ID: h2b3c4d5e6f7
Revises: g1a2b3c4d5e6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "h2b3c4d5e6f7"
down_revision: str | Sequence[str] | None = "g1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Личный переключатель 2FA -------------------------------------
    # server_default=True: для уже существующих админов 2FA остаётся включён,
    # как и до миграции. Временные учётные записи им не пользуются.
    op.add_column(
        "web_users",
        sa.Column(
            "two_factor_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )

    # --- Полный аудит действий веб-панели ------------------------------
    op.add_column("logs", sa.Column("actor_type", sa.String(length=16), nullable=True))
    op.add_column("logs", sa.Column("actor_name", sa.String(length=64), nullable=True))
    op.add_column("logs", sa.Column("http_method", sa.String(length=8), nullable=True))
    op.add_column("logs", sa.Column("path", sa.String(length=255), nullable=True))
    op.add_column("logs", sa.Column("status_code", sa.Integer(), nullable=True))
    op.add_column("logs", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.add_column("logs", sa.Column("ip_address", sa.String(length=64), nullable=True))
    op.add_column(
        "logs",
        sa.Column(
            "is_mutation",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_index("ix_logs_actor_created", "logs", ["actor_type", "created_at"])
    op.create_index("ix_logs_http_created", "logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_logs_http_created", table_name="logs")
    op.drop_index("ix_logs_actor_created", table_name="logs")

    op.drop_column("logs", "is_mutation")
    op.drop_column("logs", "ip_address")
    op.drop_column("logs", "duration_ms")
    op.drop_column("logs", "status_code")
    op.drop_column("logs", "path")
    op.drop_column("logs", "http_method")
    op.drop_column("logs", "actor_name")
    op.drop_column("logs", "actor_type")

    op.drop_column("web_users", "two_factor_enabled")
