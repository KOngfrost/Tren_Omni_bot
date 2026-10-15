"""Add attachment and forward fields to vk_outbox table.

Revision ID: m7a8b9c0d1e2
Revises: l6f7a8b9c0d1
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "m7a8b9c0d1e2"
down_revision: str | Sequence[str] | None = "l6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vk_outbox",
        sa.Column(
            "attachment",
            sa.String(length=512),
            nullable=True,
        ),
    )
    op.add_column(
        "vk_outbox",
        sa.Column(
            "forward",
            sa.Text(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("vk_outbox", "forward")
    op.drop_column("vk_outbox", "attachment")
