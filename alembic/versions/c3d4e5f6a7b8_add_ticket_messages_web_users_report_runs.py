"""add ticket_messages, web_users, report_runs

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-08-31 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создаёт таблицы истории заявок, веб-пользователей и фактов отправки отчётов."""

    # --- ticket_messages: история общения по заявке ---
    op.create_table(
        "ticket_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column(
            "author_type",
            sa.Enum("USER", "ADMIN", "SYSTEM", name="messageauthortype"),
            nullable=False,
        ),
        sa.Column("author_vk_id", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "author_type IN ('USER', 'ADMIN', 'SYSTEM')",
            name="ck_ticket_messages_author_type",
        ),
    )
    op.create_index("ix_ticket_messages_ticket_id", "ticket_messages", ["ticket_id"])

    # --- web_users: пользователи веб-админки с хешами паролей ---
    op.create_table(
        "web_users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("SUPERADMIN", "DEPARTMENT_ADMIN", "VIEWER", name="webrole"),
            nullable=False,
        ),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username", name="uq_web_users_username"),
    )

    # --- report_runs: защита от повторной отправки ежедневных отчётов ---
    op.create_table(
        "report_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="sent"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_date", name="uq_report_runs_report_date"),
    )


def downgrade() -> None:
    """Удаляет новые таблицы."""
    op.drop_table("report_runs")
    op.drop_table("web_users")
    op.drop_index("ix_ticket_messages_ticket_id", table_name="ticket_messages")
    op.drop_table("ticket_messages")
    sa.Enum(name="messageauthortype").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="webrole").drop(op.get_bind(), checkfirst=True)
