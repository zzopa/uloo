"""add workspace isolation to agent and team definitions

Revision ID: 9f3c2a1d7b6e
Revises: 42f419ca1baf
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9f3c2a1d7b6e"
down_revision: str | Sequence[str] | None = "42f419ca1baf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_WORKSPACE_ID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    """Scope all definitions and memberships to a Dify workspace."""
    op.add_column("agent_definitions", sa.Column("workspace_id", sa.UUID(), nullable=True))
    op.add_column("team_definitions", sa.Column("workspace_id", sa.UUID(), nullable=True))
    op.add_column("team_members", sa.Column("workspace_id", sa.UUID(), nullable=True))

    op.execute(
        sa.text("UPDATE agent_definitions SET workspace_id = CAST(:workspace AS uuid)").bindparams(
            workspace=LEGACY_WORKSPACE_ID
        )
    )
    op.execute(
        sa.text("UPDATE team_definitions SET workspace_id = CAST(:workspace AS uuid)").bindparams(
            workspace=LEGACY_WORKSPACE_ID
        )
    )
    op.execute(
        """
        UPDATE team_members AS member
        SET workspace_id = team.workspace_id
        FROM team_definitions AS team
        WHERE member.team_id = team.id
        """
    )

    op.alter_column("agent_definitions", "workspace_id", nullable=False)
    op.alter_column("team_definitions", "workspace_id", nullable=False)
    op.alter_column("team_members", "workspace_id", nullable=False)

    op.drop_index("ix_agent_definitions_key", table_name="agent_definitions")
    op.drop_index("ix_team_definitions_key", table_name="team_definitions")
    op.create_index("ix_agent_definitions_workspace_id", "agent_definitions", ["workspace_id"])
    op.create_index("ix_team_definitions_workspace_id", "team_definitions", ["workspace_id"])
    op.create_index("ix_team_members_workspace_id", "team_members", ["workspace_id"])
    op.create_index(
        "ix_agent_workspace_created", "agent_definitions", ["workspace_id", "created_at"]
    )
    op.create_index(
        "ix_team_workspace_created", "team_definitions", ["workspace_id", "created_at"]
    )
    op.create_unique_constraint(
        "uq_agent_workspace_key", "agent_definitions", ["workspace_id", "key"]
    )
    op.create_unique_constraint(
        "uq_team_workspace_key", "team_definitions", ["workspace_id", "key"]
    )


def downgrade() -> None:
    """Remove workspace scope when keys are globally unique."""
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT key FROM agent_definitions GROUP BY key HAVING count(*) > 1)
             OR EXISTS (SELECT key FROM team_definitions GROUP BY key HAVING count(*) > 1) THEN
            RAISE EXCEPTION 'cannot downgrade: duplicate keys exist across workspaces';
          END IF;
        END $$
        """
    )
    op.drop_constraint("uq_team_workspace_key", "team_definitions", type_="unique")
    op.drop_constraint("uq_agent_workspace_key", "agent_definitions", type_="unique")
    op.drop_index("ix_team_workspace_created", table_name="team_definitions")
    op.drop_index("ix_agent_workspace_created", table_name="agent_definitions")
    op.drop_index("ix_team_members_workspace_id", table_name="team_members")
    op.drop_index("ix_team_definitions_workspace_id", table_name="team_definitions")
    op.drop_index("ix_agent_definitions_workspace_id", table_name="agent_definitions")
    op.create_index("ix_team_definitions_key", "team_definitions", ["key"], unique=True)
    op.create_index("ix_agent_definitions_key", "agent_definitions", ["key"], unique=True)
    op.drop_column("team_members", "workspace_id")
    op.drop_column("team_definitions", "workspace_id")
    op.drop_column("agent_definitions", "workspace_id")
