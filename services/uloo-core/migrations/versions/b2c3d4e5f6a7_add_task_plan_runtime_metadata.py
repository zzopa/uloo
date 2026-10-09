"""add task plan runtime metadata

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("task_plans", sa.Column("runtime_type", sa.String(length=32), nullable=False, server_default="agno"))
    op.add_column("task_plans", sa.Column("is_mock", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("task_plans", sa.Column("planner_run_id", sa.String(length=256), nullable=True))
    op.add_column("task_plans", sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("task_plans", "usage")
    op.drop_column("task_plans", "planner_run_id")
    op.drop_column("task_plans", "is_mock")
    op.drop_column("task_plans", "runtime_type")
