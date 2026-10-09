"""add task and task_plan tables

Revision ID: a1b2c3d4e5f6
Revises: 9f3c2a1d7b6e
Create Date: 2026-09-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "9f3c2a1d7b6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add durable Task and TaskPlan tables for the main flow."""
    op.create_table(
        "tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=True),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("current_plan_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["team_definitions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_workspace_created", "tasks", ["workspace_id", "created_at"])
    op.create_index("ix_task_workspace_status", "tasks", ["workspace_id", "status"])
    op.create_index("ix_tasks_workspace_id", "tasks", ["workspace_id"])
    op.create_index("ix_tasks_team_id", "tasks", ["team_id"])

    op.create_table(
        "task_plans",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("assumptions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("questions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("recommended_team_id", sa.UUID(), nullable=True),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("approval_status", sa.String(length=32), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["recommended_team_id"], ["team_definitions.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "version", name="uq_task_plan_version"),
    )
    op.create_index("ix_task_plan_task_created", "task_plans", ["task_id", "created_at"])
    op.create_index("ix_task_plans_task_id", "task_plans", ["task_id"])


def downgrade() -> None:
    """Remove the main-flow tables."""
    op.drop_index("ix_task_plans_task_id", table_name="task_plans")
    op.drop_index("ix_task_plan_task_created", table_name="task_plans")
    op.drop_table("task_plans")
    op.drop_index("ix_tasks_team_id", table_name="tasks")
    op.drop_index("ix_tasks_workspace_id", table_name="tasks")
    op.drop_index("ix_task_workspace_status", table_name="tasks")
    op.drop_index("ix_task_workspace_created", table_name="tasks")
    op.drop_table("tasks")
