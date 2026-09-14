"""Agent definition model."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class AgentDefinition(Base):
    """Agent definition - configurable agent that can be part of a Team."""

    __tablename__ = "agent_definitions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(256), nullable=False)
    instructions: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    model_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    tool_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    knowledge_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    output_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def to_dict(self, include_sensitive: bool = False) -> dict:
        """Serialize to dict. Never expose API keys."""
        return {
            "id": str(self.id),
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "role": self.role,
            "instructions": self.instructions,
            "model_ref": self.model_ref,
            "tool_refs": self.tool_refs,
            "knowledge_refs": self.knowledge_refs,
            "output_schema": self.output_schema,
            "enabled": self.enabled,
            "version": self.version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
