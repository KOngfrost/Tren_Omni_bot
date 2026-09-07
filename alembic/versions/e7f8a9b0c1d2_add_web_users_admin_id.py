"""add admin_id to web_users for cascade delete

Revision ID: e7f8a9b0c1d2
Revises: c3d4e5f6a7b8
Create Date: 2026-09-07 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7f8a9b0c1d2'
down_revision: str | Sequence[str] | None = 'c3d4e5f6a7b8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Добавляет колонку admin_id в web_users с FK на admins.id (CASCADE DELETE)."""
    op.add_column(
        'web_users',
        sa.Column(
            'admin_id',
            sa.Integer(),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        'fk_web_users_admin_id',
        'web_users',
        'admins',
        ['admin_id'],
        ['id'],
        ondelete='CASCADE',
    )


def downgrade() -> None:
    """Удаляет колонку admin_id и внешний ключ."""
    op.drop_constraint('fk_web_users_admin_id', 'web_users', type_='foreignkey')
    op.drop_column('web_users', 'admin_id')
