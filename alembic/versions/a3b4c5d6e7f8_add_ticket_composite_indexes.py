"""add ticket composite indexes

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-09-18 01:40:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3b4c5d6e7f8"
down_revision: str | Sequence[str] | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add composite indexes to tickets for reporting and dashboard queries."""
    op.create_index(
        "ix_tickets_dept_status",
        "tickets",
        ["department_id", "status"],
    )
    op.create_index(
        "ix_tickets_status_created_at",
        "tickets",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_tickets_dept_created_at",
        "tickets",
        ["department_id", "created_at"],
    )


def downgrade() -> None:
    """Drop ticket composite indexes."""
    op.drop_index("ix_tickets_dept_created_at", table_name="tickets")
    op.drop_index("ix_tickets_status_created_at", table_name="tickets")
    op.drop_index("ix_tickets_dept_status", table_name="tickets")
