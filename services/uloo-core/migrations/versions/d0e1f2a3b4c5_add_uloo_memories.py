# AI生成
"""Add uloo_memories table for cross-task long-term memory.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-10-22 18:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "uloo_memories",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", sa.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("category", sa.String(length=32), nullable=False, server_default="fact"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("keywords", JSONB(), nullable=False, server_default="[]"),
        sa.Column("source_run_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_uloo_memories_category", "uloo_memories", ["category"])


def downgrade() -> None:
    op.drop_index("ix_uloo_memories_category", table_name="uloo_memories")
    op.drop_table("uloo_memories")
