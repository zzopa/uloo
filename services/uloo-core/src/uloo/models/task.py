"""Task and TaskPlan models - the durable record of a user business request."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base

# Task statuses are a single-source-of-truth string list shared with schemas.
TASK_STATUSES = ("draft", "planning", "awaiting_approval", "running", "succeeded", "failed", "cancelled")
PLAN_APPROVAL_STATUSES = ("pending", "approved", "rejected")


class Task(Base):
    """A user business task, the root of the ULOO main flow."""

    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_task_workspace_created", "workspace_id", "created_at"),
        Index("ix_task_workspace_status", "workspace_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="draft", nullable=False)
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("team_definitions.id"), nullable=True, index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid.uuid4, nullable=False)
    current_plan_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    plans: Mapped[list["TaskPlan"]] = relationship(back_populates="task", cascade="all, delete-orphan", lazy="selectin")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "title": self.title,
            "query": self.query,
            "status": self.status,
            "team_id": str(self.team_id) if self.team_id else None,
            "session_id": str(self.session_id),
            "current_plan_version": self.current_plan_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TaskPlan(Base):
    """A versioned execution plan produced by the Planner and editable by the user."""

    __tablename__ = "task_plans"
    __table_args__ = (
        UniqueConstraint("task_id", "version", name="uq_task_plan_version"),
        Index("ix_task_plan_task_created", "task_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    assumptions: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    questions: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    recommended_team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("team_definitions.id"), nullable=True
    )
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    approval_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    runtime_type: Mapped[str] = mapped_column(String(32), default="agno", nullable=False)
    is_mock: Mapped[bool] = mapped_column(default=False, nullable=False)
    planner_run_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    usage: Mapped[dict[str, int] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    task: Mapped["Task"] = relationship(back_populates="plans")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "task_id": str(self.task_id),
            "version": self.version,
            "summary": self.summary,
            "assumptions": self.assumptions,
            "questions": self.questions,
            "recommended_team_id": str(self.recommended_team_id) if self.recommended_team_id else None,
            "steps": self.steps,
            "approval_status": self.approval_status,
            "rejection_reason": self.rejection_reason,
            "runtime_type": self.runtime_type,
            "is_mock": self.is_mock,
            "planner_run_id": self.planner_run_id,
            "usage": self.usage,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
