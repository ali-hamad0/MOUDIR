"""phase7 pending_reengagements table (CustomerAgent HIL artifact)

Revision ID: c7e4f8d2a1b9
Revises: ab5b5b8274b1
Create Date: 2026-06-08 00:00:00.000000

Task 7.4 — CustomerAgent: queue_reengagement writes drafts to this table so the
owner can approve re-engagement messages from the dashboard. Actual WhatsApp send
is Phase 10. LangGraph checkpoint tables are managed by AsyncPostgresSaver.setup()
— they are NOT here (the library owns its schema, and setup() is idempotent).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7e4f8d2a1b9"
down_revision: str | Sequence[str] | None = "ab5b5b8274b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "pending_reengagements",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("customer_name_ar", sa.String(length=255), nullable=True),
        sa.Column("draft_message_ar", sa.Text(), nullable=False),
        sa.Column("action_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pending_reengagements_tenant_id", "pending_reengagements", ["tenant_id"])
    op.create_index(
        "ix_pending_reengagements_customer_id", "pending_reengagements", ["customer_id"]
    )
    op.create_index("ix_pending_reengagements_action_key", "pending_reengagements", ["action_key"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_pending_reengagements_action_key", table_name="pending_reengagements")
    op.drop_index("ix_pending_reengagements_customer_id", table_name="pending_reengagements")
    op.drop_index("ix_pending_reengagements_tenant_id", table_name="pending_reengagements")
    op.drop_table("pending_reengagements")
