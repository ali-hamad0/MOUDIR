"""phase10 chat sessions + messages (owner dashboard chat history)

Revision ID: a3c9e7f1b2d4
Revises: f1a8b3c2d9e5
Create Date: 2026-06-10 00:00:00.000000

Phase 10 — the owner chat page gets saved conversations. One chat_sessions row
per conversation (its id doubles as the LangGraph thread session_id, so
supervisor state — e.g. a pending stock-edit confirmation — is scoped per
conversation). chat_messages holds every bubble. The Wall: tenant_id on both
tables, non-nullable, CASCADE on tenant delete.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3c9e7f1b2d4"
down_revision: str | Sequence[str] | None = "f1a8b3c2d9e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "chat_sessions",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_sessions_tenant_updated", "chat_sessions", ["tenant_id", "updated_at"])

    op.create_table(
        "chat_messages",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        # Strict insertion order. created_at can't order bubbles: Postgres now()
        # is transaction-constant, so two bubbles written in one request tie.
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("agent", sa.String(length=32), nullable=True),
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
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_messages_tenant_session", "chat_messages", ["tenant_id", "session_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_chat_messages_tenant_session", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_sessions_tenant_updated", table_name="chat_sessions")
    op.drop_table("chat_sessions")
