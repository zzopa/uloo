"""Durable Agno Team runs and their observable events."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base

RUN_STATUSES = ("running", "succeeded", "failed", "cancelled")


class Run(Base):
    """One persisted execution of an approved task plan."""

    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_run_workspace_created", "workspace_id", "created_at"),
        Index("ix_run_workspace_status", "workspace_id", "status"),
        Index("ix_run_task_created", "task_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_plans.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("team_definitions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    runtime_type: Mapped[str] = mapped_column(String(32), default="agno", nullable=False)
    is_mock: Mapped[bool] = mapped_column(default=False, nullable=False)
    agno_run_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    configuration_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    usage: Mapped[dict[str, int] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    events: Mapped[list["RunEvent"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin", order_by="RunEvent.sequence"
    )

    def to_dict(self, *, include_events: bool = False) -> dict[str, Any]:
        result = {
            "id": str(self.id),
            "workspace_id": str(self.workspace_id),
            "task_id": str(self.task_id),
            "plan_id": str(self.plan_id),
            "team_id": str(self.team_id),
            "status": self.status,
            "runtime_type": self.runtime_type,
            "is_mock": self.is_mock,
            "agno_run_id": self.agno_run_id,
            "input_text": self.input_text,
            "configuration_snapshot": self.configuration_snapshot,
            "output": self.output,
            "usage": self.usage,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_events:
            result["events"] = [event.to_dict() for event in self.events]
        return result


class RunEvent(Base):
    """Safe observable state transition emitted by a run."""

    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_run_event_sequence"),
        Index("ix_run_event_run_created", "run_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), default="system", nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    run: Mapped[Run] = relationship(back_populates="events")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "run_id": str(self.run_id),
            "sequence": self.sequence,
            "event_type": self.event_type,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "status": self.status,
            "payload": self.payload,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
