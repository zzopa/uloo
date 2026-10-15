"""Add tool_type=builtin to existing tool resource configs.

Data-only migration: the ``config`` JSONB column already exists; we merely
stamp existing tool resources with ``tool_type: "builtin"`` so the new
``validate_config`` dispatcher has an explicit value to read.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-09
"""

from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        UPDATE resource_definitions
        SET config = config || '{"tool_type": "builtin"}'::jsonb
        WHERE kind = 'tools'
          AND NOT (config ? 'tool_type')
        """
    )


def downgrade():
    op.execute(
        """
        UPDATE resource_definitions
        SET config = config - 'tool_type'
        WHERE kind = 'tools'
          AND config ? 'tool_type'
          AND config->>'tool_type' = 'builtin'
        """
    )
