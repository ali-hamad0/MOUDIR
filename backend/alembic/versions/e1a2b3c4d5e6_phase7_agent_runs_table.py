"""phase7 agent_runs table (cost tracking, Task 7.9)

Revision ID: e1a2b3c4d5e6
Revises: c7e4f8d2a1b9
Create Date: 2026-06-08 00:00:00.000000

Task 7.9 — CostTrackingCallback writes one row per LLM call. The Wall:
tenant_id is non-nullable, ON DELETE CASCADE so a removed tenant cleans up
its cost rows automatically. The composite index (tenant_id, created_at)
covers the admin daily-aggregation query.

LangGraph checkpoint tables are NOT in Alembic — they are managed by
AsyncPostgresSaver.setup() which is idempotent and runs at startup. See
docs/DECISIONS.md Phase 7 section for the rationale.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "c7e4f8d2a1b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "agent_runs",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_name", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=64), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cost_usd", sa.Numeric(precision=10, scale=6), nullable=False, server_default="0"
        ),
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
    )
    op.create_index("ix_agent_runs_tenant_created", "agent_runs", ["tenant_id", "created_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_agent_runs_tenant_created", table_name="agent_runs")
    op.drop_table("agent_runs")
