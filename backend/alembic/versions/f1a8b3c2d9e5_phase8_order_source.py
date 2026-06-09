"""phase8 orders.source column (manual order entry, Task 8.5)

Revision ID: f1a8b3c2d9e5
Revises: e1a2b3c4d5e6
Create Date: 2026-06-09 00:00:00.000000

Task 8.5 — POST /orders/manual creates orders with source='manual'.
Agent-created orders default to 'agent' (NULL treated as 'agent' for backward
compatibility with rows written before this migration).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1a8b3c2d9e5"
down_revision: str | Sequence[str] | None = "e1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add source column to orders (nullable; NULL = 'agent' for legacy rows)."""
    op.add_column(
        "orders",
        sa.Column("source", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    """Drop source column from orders."""
    op.drop_column("orders", "source")
