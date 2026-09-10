"""add login_attempts and vk_outbox

Revision ID: d5e6f7a8b9c0
Revises: c3d4e5f6a7b8
Create Date: 2026-09-01 12:00:00.000000

Почему нужно:
- login_attempts: rate-limit входа больше не живёт в памяти процесса —
  переживает рестарты панели и синхронизирован между экземплярами.
- vk_outbox: сообщения в VK пишутся в той же транзакции, что и изменение
  заявки, и доставляются фоновым воркером (устраняет «ответ в VK,
  которого нет в БД»).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_login_attempts_ip_created", "login_attempts", ["ip", "attempted_at"])

    op.create_table(
        "vk_outbox",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("vk_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vk_outbox_status_created", "vk_outbox", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_vk_outbox_status_created", table_name="vk_outbox")
    op.drop_table("vk_outbox")
    op.drop_index("ix_login_attempts_ip_created", table_name="login_attempts")
    op.drop_table("login_attempts")
