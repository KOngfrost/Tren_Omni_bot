"""Add last_seen_at to users table.

Revision ID: k5e6f7a8b9c0
Revises: j4d5e6f7a8b9
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "k5e6f7a8b9c0"
down_revision: str | Sequence[str] | None = "j4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )
    op.create_index("ix_users_last_seen_at", "users", ["last_seen_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_users_last_seen_at", table_name="users")
    op.drop_column("users", "last_seen_at")
