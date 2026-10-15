"""Add tab permissions to web_users table.

Revision ID: l6f7a8b9c0d1
Revises: k5e6f7a8b9c0
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "l6f7a8b9c0d1"
down_revision: str | Sequence[str] | None = "k5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "web_users",
        sa.Column(
            "can_access_partnerships",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "web_users",
        sa.Column(
            "can_access_broadcast",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "web_users",
        sa.Column(
            "can_access_logs",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("web_users", "can_access_logs")
    op.drop_column("web_users", "can_access_broadcast")
    op.drop_column("web_users", "can_access_partnerships")
