"""phase11 subscription billing (manual payments, period tracking)

Revision ID: b7c1d2e3f4a5
Revises: a3c9e7f1b2d4
Create Date: 2026-06-12 00:00:00.000000

Manual billing for the Lebanon market: tenants get a subscription_status
("trialing" until the first payment) and a current_period_end; every payment
the founder records lands in subscription_payments and extends the period.
No payment gateway in v1 — Whish/OMT/cash happen out-of-band. The Wall:
tenant_id on subscription_payments, non-nullable, indexed, CASCADE on tenant
delete.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7c1d2e3f4a5"
down_revision: str | Sequence[str] | None = "a3c9e7f1b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default backfills existing tenants as "trialing"; the ORM default
    # governs new rows.
    op.add_column(
        "tenants",
        sa.Column(
            "subscription_status",
            sa.String(length=16),
            nullable=False,
            server_default="trialing",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column("current_period_end", sa.Date(), nullable=True),
    )

    op.create_table(
        "subscription_payments",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("amount_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("plan_tier", sa.String(length=32), nullable=False),
        sa.Column("period_end_after", sa.Date(), nullable=False),
        sa.Column("recorded_by", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(["recorded_by"], ["admins.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_subscription_payments_tenant_created",
        "subscription_payments",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_subscription_payments_tenant_created", table_name="subscription_payments")
    op.drop_table("subscription_payments")
    op.drop_column("tenants", "current_period_end")
    op.drop_column("tenants", "subscription_status")
