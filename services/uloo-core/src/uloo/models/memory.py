# AI生成
"""Long-term Memory persistence model for cross-task facts and preferences."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from ..db import Base


class MemoryItem(Base):
    """A durable memory item (fact, preference, or guideline) within a workspace."""

    __tablename__ = "uloo_memories"

    id = sa.Column(sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = sa.Column(sa.UUID(as_uuid=True), nullable=False, index=True)
    category = sa.Column(sa.String(32), nullable=False, default="fact", index=True)
    content = sa.Column(sa.Text, nullable=False)
    keywords = sa.Column(JSONB, nullable=False, default=list)
    embedding = sa.Column(Vector(), nullable=True)
    source_run_id = sa.Column(sa.UUID(as_uuid=True), nullable=True)
    created_at = sa.Column(sa.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at = sa.Column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "workspace_id": str(self.workspace_id),
            "category": self.category,
            "content": self.content,
            "keywords": self.keywords or [],
            "has_embedding": self.embedding is not None,
            "source_run_id": str(self.source_run_id) if self.source_run_id else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
