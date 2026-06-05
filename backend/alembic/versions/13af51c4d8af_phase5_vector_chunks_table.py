"""phase5 vector_chunks table

Revision ID: 13af51c4d8af
Revises: af97ae35f9b5
Create Date: 2026-06-05 14:59:55.289509

"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "13af51c4d8af"
down_revision: str | Sequence[str] | None = "af97ae35f9b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Embedding width — must match models.EMBEDDING_DIM / Settings.embedding_dim
# (text-embedding-004 = 768). Changing it requires a new migration.
_DIM = 768


def upgrade() -> None:
    """Upgrade schema."""
    # The pgvector extension must exist before a VECTOR column can be created. The db
    # image (pgvector/pgvector:pg16) ships the extension; this enables it. Idempotent.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "vector_chunks",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("corpus", sa.String(length=16), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(dim=_DIM), nullable=False),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "corpus",
            "source_type",
            "source_id",
            "chunk_index",
            name="uq_vector_chunk_source",
        ),
    )
    op.create_index(op.f("ix_vector_chunks_corpus"), "vector_chunks", ["corpus"], unique=False)
    op.create_index(
        op.f("ix_vector_chunks_tenant_id"), "vector_chunks", ["tenant_id"], unique=False
    )
    # ANN index for cosine-distance search (the retrieval order-by uses
    # cosine_distance, matching the unit-norm vectors the embedding clients produce).
    # HNSW gives good recall/latency without a training step. The tenant_id/corpus
    # WHERE still runs first (the Wall) — this index accelerates the similarity scan
    # within the filtered set.
    op.create_index(
        "ix_vector_chunks_embedding_hnsw",
        "vector_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_vector_chunks_embedding_hnsw", table_name="vector_chunks")
    op.drop_index(op.f("ix_vector_chunks_tenant_id"), table_name="vector_chunks")
    op.drop_index(op.f("ix_vector_chunks_corpus"), table_name="vector_chunks")
    op.drop_table("vector_chunks")
    # Leave the `vector` extension in place — other objects may rely on it and
    # dropping it is not the table migration's concern.
