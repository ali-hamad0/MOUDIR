"""phase11 whish pay checkouts + gateway-recorded payments

Revision ID: c8d2e3f4a5b6
Revises: b7c1d2e3f4a5
Create Date: 2026-06-12 00:00:00.000000

Online subscriptions through the Whish Pay collect API: one billing_checkouts
row per attempt (pending → paid/failed after SERVER-SIDE verification with
Whish). subscription_payments.recorded_by becomes nullable — gateway payments
are recorded by the system, not a founder. The Wall: tenant_id on
billing_checkouts, non-nullable, indexed, CASCADE on tenant delete.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8d2e3f4a5b6"
down_revision: str | Sequence[str] | None = "b7c1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "subscription_payments",
        "recorded_by",
        existing_type=sa.UUID(),
        nullable=True,
    )

    op.create_table(
        "billing_checkouts",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("amount_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column("external_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("collect_url", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_id"),
    )
    op.create_index("ix_billing_checkouts_tenant", "billing_checkouts", ["tenant_id"])
    op.create_index("ix_billing_checkouts_external", "billing_checkouts", ["external_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_billing_checkouts_external", table_name="billing_checkouts")
    op.drop_index("ix_billing_checkouts_tenant", table_name="billing_checkouts")
    op.drop_table("billing_checkouts")
    op.alter_column(
        "subscription_payments",
        "recorded_by",
        existing_type=sa.UUID(),
        nullable=False,
    )
