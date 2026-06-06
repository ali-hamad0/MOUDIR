"""phase6 tenant data_source column

Revision ID: ab5b5b8274b1
Revises: 13af51c4d8af
Create Date: 2026-06-06 06:27:00.654783

Adds tenants.data_source (Phase 6, Task 6.2): "synthetic" for tenants created by
the ML history seeder, NULL/"real" otherwise. Nullable so real signup is unaffected.

NOTE: autogenerate also proposed dropping ix_vector_chunks_embedding_hnsw. That is a
false positive — the pgvector HNSW index is created by raw DDL in the Phase 5 vector
migration and cannot be modelled by SQLAlchemy's metadata, so every autogenerate run
will keep "rediscovering" it as removable. Dropping it would destroy the RAG retrieval
index. It is intentionally NOT touched here; this migration only adds the column.

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ab5b5b8274b1"
down_revision: str | Sequence[str] | None = "13af51c4d8af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("tenants", sa.Column("data_source", sa.String(length=16), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("tenants", "data_source")
