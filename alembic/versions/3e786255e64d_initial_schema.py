"""initial schema

Revision ID: 3e786255e64d
Revises:
Create Date: 2026-08-29 19:37:26.163603

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3e786255e64d"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- Enum types ---
    ticketstatus = postgresql.ENUM(
        "NEW",
        "IN_PROGRESS",
        "TRANSFERRED_ADMIN",
        "TRANSFERRED_HOUSEKEEPING",
        "COMPLETED",
        "COMPLETED_AUTO",
        "ANONYMOUS",
        name="ticketstatus",
        create_type=False,
    )
    ticketstatus.create(op.get_bind(), checkfirst=True)

    userrole = postgresql.ENUM(
        "ADMIN",
        "SUPERADMIN",
        name="userrole",
        create_type=False,
    )
    userrole.create(op.get_bind(), checkfirst=True)

    # --- departments ---
    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.UniqueConstraint("name", name="departments_name_key"),
    )

    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("vk_id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=True),
        sa.Column("dormitory", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("vk_id", name="users_vk_id_key"),
    )

    # --- admins ---
    op.create_table(
        "admins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "department_id",
            sa.Integer(),
            sa.ForeignKey("departments.id"),
            nullable=True,
        ),
        sa.Column("role", userrole, nullable=True),
    )

    # --- tickets ---
    op.create_table(
        "tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "department_id",
            sa.Integer(),
            sa.ForeignKey("departments.id"),
            nullable=True,
        ),
        sa.Column("topic", sa.String(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", ticketstatus, nullable=True),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("is_anonymous", sa.Boolean(), nullable=True),
        sa.Column("auto_closed", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )

    # --- knowledge_base ---
    op.create_table(
        "knowledge_base",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "department_id",
            sa.Integer(),
            sa.ForeignKey("departments.id"),
            nullable=False,
        ),
        sa.Column("keywords", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )

    # --- faq_nodes ---
    op.create_table(
        "faq_nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "department_id",
            sa.Integer(),
            sa.ForeignKey("departments.id"),
            nullable=False,
        ),
        sa.Column(
            "parent_id",
            sa.Integer(),
            sa.ForeignKey("faq_nodes.id"),
            nullable=True,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column("final_answer", sa.Text(), nullable=True),
        sa.Column("button_text", sa.String(), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )

    # --- subscriptions ---
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "department_id",
            sa.Integer(),
            sa.ForeignKey("departments.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "user_id",
            "department_id",
            name="uq_subscription_user_department",
        ),
    )

    # --- events ---
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "department_id",
            sa.Integer(),
            sa.ForeignKey("departments.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )

    # --- registrations ---
    op.create_table(
        "registrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id"),
            nullable=False,
        ),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )

    # --- logs ---
    op.create_table(
        "logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("logs")
    op.drop_table("registrations")
    op.drop_table("events")
    op.drop_table("subscriptions")
    op.drop_table("faq_nodes")
    op.drop_table("knowledge_base")
    op.drop_table("tickets")
    op.drop_table("admins")
    op.drop_table("users")
    op.drop_table("departments")

    ticketstatus = postgresql.ENUM(
        "NEW",
        "IN_PROGRESS",
        "TRANSFERRED_ADMIN",
        "TRANSFERRED_HOUSEKEEPING",
        "COMPLETED",
        "COMPLETED_AUTO",
        "ANONYMOUS",
        name="ticketstatus",
    )
    ticketstatus.drop(op.get_bind(), checkfirst=True)

    userrole = postgresql.ENUM(
        "ADMIN",
        "SUPERADMIN",
        name="userrole",
    )
    userrole.drop(op.get_bind(), checkfirst=True)
