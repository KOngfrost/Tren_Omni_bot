"""Add foreign key with cascade delete from ticket_messages to tickets.

Revision ID: n8b9c0d1e2f3
Revises: m7a8b9c0d1e2
"""

from collections.abc import Sequence

from alembic import op

revision: str = "n8b9c0d1e2f3"
down_revision: str | Sequence[str] | None = "m7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Удаляем осиротевшие записи (если такие есть), чтобы создание FK не упало
    op.execute("DELETE FROM ticket_messages WHERE ticket_id NOT IN (SELECT id FROM tickets)")
    # 2. Создаем внешний ключ с каскадным удалением
    op.create_foreign_key(
        "fk_ticket_messages_ticket_id_cascade",
        "ticket_messages",
        "tickets",
        ["ticket_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_ticket_messages_ticket_id_cascade",
        "ticket_messages",
        type_="foreignkey",
    )
