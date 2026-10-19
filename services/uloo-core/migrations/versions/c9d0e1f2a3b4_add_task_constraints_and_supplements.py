# AI生成
"""Add constraints and user_supplements to tasks table.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2025-01-15 00:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("constraints", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("user_supplements", JSONB(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("tasks", "user_supplements")
    op.drop_column("tasks", "constraints")
