"""Add partnership_requests table.

Revision ID: f3a4b5c6d7e8
Revises: f2a3b4c5d6e7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: str | Sequence[str] | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "partnership_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("vk_id", sa.BigInteger(), nullable=False),
        sa.Column("user_name", sa.String(length=255), nullable=True),
        sa.Column("proposal_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="new"),
        sa.Column("contact_info", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_partnership_requests_vk_id", "partnership_requests", ["vk_id"])
    op.create_index(
        "ix_partnership_requests_status_created",
        "partnership_requests",
        ["status", "created_at"],
    )
    op.alter_column("knowledge_base", "department_id", nullable=True)


def downgrade() -> None:
    op.alter_column("knowledge_base", "department_id", nullable=False)
    op.drop_index("ix_partnership_requests_status_created", table_name="partnership_requests")
    op.drop_index("ix_partnership_requests_vk_id", table_name="partnership_requests")
    op.drop_table("partnership_requests")
