"""Workspace tool and skill libraries and Agent bindings."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_definitions", sa.Column("skill_refs", postgresql.JSONB(), nullable=False, server_default="[]"))
    op.create_table(
        "resource_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.String(2000), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("workspace_id", "kind", "key", name="uq_resource_workspace_kind_key"),
    )
    op.create_index("ix_resource_definitions_workspace_id", "resource_definitions", ["workspace_id"])


def downgrade():
    op.drop_table("resource_definitions")
    op.drop_column("agent_definitions", "skill_refs")
