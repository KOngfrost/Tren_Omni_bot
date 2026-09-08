"""Use 64-bit VK identifiers.

Revision ID: f1a2b3c4d5e6
Revises: e7f8a9b0c1d2
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "99c64c6d7318"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table, column in (
        ("users", "vk_id"),
        ("vk_outbox", "vk_id"),
        ("ticket_messages", "author_vk_id"),
    ):
        op.alter_column(table, column, existing_type=sa.Integer(), type_=sa.BigInteger())


def downgrade() -> None:
    for table, column in (
        ("ticket_messages", "author_vk_id"),
        ("vk_outbox", "vk_id"),
        ("users", "vk_id"),
    ):
        op.alter_column(table, column, existing_type=sa.BigInteger(), type_=sa.Integer())
