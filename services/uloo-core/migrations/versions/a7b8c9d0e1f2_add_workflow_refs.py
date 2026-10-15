# AI生成
"""Add workflow_refs column to agent_definitions (S1-8).

Each agent can now reference Dify workflow apps as execution tools.
The column is a JSONB array of workflow app IDs, defaulting to empty.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agent_definitions",
        sa.Column("workflow_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
    )


def downgrade():
    op.drop_column("agent_definitions", "workflow_refs")
