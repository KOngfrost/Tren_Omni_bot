"""Add composite index on login_attempts(ip, success, attempted_at) (PERF-001).

Revision ID: i3c4d5e6f7a8
Revises: h2b3c4d5e6f7
"""

from collections.abc import Sequence

from alembic import op

revision: str = "i3c4d5e6f7a8"
down_revision: str | Sequence[str] | None = "h2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_login_attempts_ip_success_attempted",
        "login_attempts",
        ["ip", "success", "attempted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_login_attempts_ip_success_attempted", table_name="login_attempts")
