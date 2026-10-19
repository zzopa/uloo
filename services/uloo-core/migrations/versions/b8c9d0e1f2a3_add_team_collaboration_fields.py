# AI生成
"""Add team collaboration fields: review_policy, completion, handoff_context, total_token_budget.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2025-01-15 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "team_definitions",
        sa.Column("review_policy", JSONB, nullable=False, server_default='{"max_rounds": 3, "require_unanimous": false, "criteria": []}'),
    )
    op.add_column(
        "team_definitions",
        sa.Column("completion", JSONB, nullable=False, server_default='{"all_members_executed": true, "no_unresolved_errors": true, "custom": ""}'),
    )
    op.add_column(
        "team_definitions",
        sa.Column("handoff_context", sa.Text, nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("team_definitions", "handoff_context")
    op.drop_column("team_definitions", "completion")
    op.drop_column("team_definitions", "review_policy")
