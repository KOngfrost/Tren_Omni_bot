"""merge: login_attempts_vk_outbox + web_users_admin_id

Revision ID: 99c64c6d7318
Revises: d5e6f7a8b9c0, e7f8a9b0c1d2
Create Date: 2026-09-07 13:26:53.155355

Merge-ревизия устраняет развилку: d5e6f7a8b9c0 (login_attempts, vk_outbox)
и e7f8a9b0c1d2 (web_users.admin_id) были порождены от одного родителя
c3d4e5f6a7b8. Схему не меняет — только объединяет ветки истории миграций,
чтобы `alembic upgrade head` снова работал (голова ровно одна).
"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = '99c64c6d7318'
down_revision: str | Sequence[str] | None = ('d5e6f7a8b9c0', 'e7f8a9b0c1d2')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Схему не меняет: обе ветки уже применены до этой точки."""
    pass


def downgrade() -> None:
    """Обратная операция для merge-ревизии не имеет смысла (точка ветвления)."""
    pass

