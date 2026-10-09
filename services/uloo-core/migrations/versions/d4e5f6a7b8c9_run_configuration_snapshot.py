"""Preserve configuration used by each run, without inventing history for old runs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("configuration_snapshot", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "configuration_snapshot")
