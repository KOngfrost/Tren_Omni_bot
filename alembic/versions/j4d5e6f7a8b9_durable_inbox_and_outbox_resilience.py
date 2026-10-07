"""Add vk_inbox table and vk_outbox random_id and next_retry_at.

Revision ID: j4d5e6f7a8b9
Revises: i3c4d5e6f7a8
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "j4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "i3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create vk_inbox table
    op.create_table(
        "vk_inbox",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("group_id", sa.BigInteger(), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vk_inbox_event_id", "vk_inbox", ["event_id"], unique=False)
    op.create_index(
        "ix_vk_inbox_status_created", "vk_inbox", ["status", "created_at"], unique=False
    )

    # 2. Add random_id and next_retry_at to vk_outbox
    op.add_column("vk_outbox", sa.Column("random_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "vk_outbox", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_vk_outbox_status_next_retry",
        "vk_outbox",
        ["status", "next_retry_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_vk_outbox_status_next_retry", table_name="vk_outbox")
    op.drop_column("vk_outbox", "next_retry_at")
    op.drop_column("vk_outbox", "random_id")
    op.drop_index("ix_vk_inbox_status_created", table_name="vk_inbox")
    op.drop_index("ix_vk_inbox_event_id", table_name="vk_inbox")
    op.drop_table("vk_inbox")
