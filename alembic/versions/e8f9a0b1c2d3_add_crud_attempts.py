"""add crud_attempts table for multi-process rate limiting

Revision ID: e8f9a0b1c2d3
Revises: e7f8a9b0c1d2
Create Date: 2026-09-08 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e8f9a0b1c2d3'
down_revision: str | Sequence[str] | None = 'e7f8a9b0c1d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создаёт таблицу crud_attempts для многопроцессного rate limiting."""
    op.create_table(
        'crud_attempts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('ip', sa.String(64), nullable=False),
        sa.Column('action', sa.String(64), nullable=False,
                   comment='Краткое описание действия (e.g. ticket_status_change)'),
        sa.Column('attempted_at', sa.DateTime(timezone=True),
                   server_default=sa.func.now(), nullable=False),
        sa.Index('ix_crud_attempts_ip_action_attempted',
                 'ip', 'action', 'attempted_at'),
    )


def downgrade() -> None:
    """Удаляет таблицу crud_attempts."""
    op.drop_table('crud_attempts')
    op.drop_index('ix_crud_attempts_ip_action_attempted')
