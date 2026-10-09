"""add durable runs and observable events

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("plan_id", sa.UUID(), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("runtime_type", sa.String(length=32), nullable=False),
        sa.Column("is_mock", sa.Boolean(), nullable=False),
        sa.Column("agno_run_id", sa.String(length=256), nullable=True),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["task_plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["team_definitions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runs_workspace_id", "runs", ["workspace_id"])
    op.create_index("ix_runs_task_id", "runs", ["task_id"])
    op.create_index("ix_runs_plan_id", "runs", ["plan_id"])
    op.create_index("ix_runs_team_id", "runs", ["team_id"])
    op.create_index("ix_run_workspace_created", "runs", ["workspace_id", "created_at"])
    op.create_index("ix_run_workspace_status", "runs", ["workspace_id", "status"])
    op.create_index("ix_run_task_created", "runs", ["task_id", "created_at"])

    op.create_table(
        "run_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_run_event_sequence"),
    )
    op.create_index("ix_run_events_workspace_id", "run_events", ["workspace_id"])
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])
    op.create_index("ix_run_event_run_created", "run_events", ["run_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_run_event_run_created", table_name="run_events")
    op.drop_index("ix_run_events_run_id", table_name="run_events")
    op.drop_index("ix_run_events_workspace_id", table_name="run_events")
    op.drop_table("run_events")
    op.drop_index("ix_run_task_created", table_name="runs")
    op.drop_index("ix_run_workspace_status", table_name="runs")
    op.drop_index("ix_run_workspace_created", table_name="runs")
    op.drop_index("ix_runs_team_id", table_name="runs")
    op.drop_index("ix_runs_plan_id", table_name="runs")
    op.drop_index("ix_runs_task_id", table_name="runs")
    op.drop_index("ix_runs_workspace_id", table_name="runs")
    op.drop_table("runs")
