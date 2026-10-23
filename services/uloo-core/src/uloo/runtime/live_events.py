# AI生成
"""Real-time event emitter for live multi-agent tracing."""

import asyncio
from contextvars import ContextVar
import uuid
from typing import Any

from sqlalchemy import func, select

from ..db import async_session_factory
from ..logging import get_logger
from ..models.run import RunEvent

logger = get_logger("uloo.runtime.live_events")

# Per-request context holding active run_id and workspace_id
_active_run_context: ContextVar[dict[str, Any] | None] = ContextVar("uloo_active_run_context", default=None)

# In-memory per-run lock to guarantee strictly sequential sequence numbers without DB collisions
_run_sequence_locks: dict[str, asyncio.Lock] = {}


def set_active_run(run_id: str | uuid.UUID, workspace_id: str | uuid.UUID) -> None:
    """Set the active run context for real-time live event streaming."""
    _active_run_context.set(
        {
            "run_id": uuid.UUID(str(run_id)),
            "workspace_id": uuid.UUID(str(workspace_id)),
        }
    )


def clear_active_run() -> None:
    """Clear the active run context."""
    _active_run_context.set(None)


def get_active_run() -> dict[str, Any] | None:
    """Get the currently active run context."""
    return _active_run_context.get()


async def _persist_live_event_async(
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
    source_type: str,
    source_id: str | None,
    status: str,
) -> None:
    run_id_str = str(run_id)
    if run_id_str not in _run_sequence_locks:
        _run_sequence_locks[run_id_str] = asyncio.Lock()

    async with _run_sequence_locks[run_id_str]:
        try:
            async with async_session_factory() as session:
                max_seq = await session.scalar(
                    select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run_id)
                )
                seq = (max_seq or 0) + 1
                event = RunEvent(
                    workspace_id=workspace_id,
                    run_id=run_id,
                    sequence=seq,
                    event_type=event_type,
                    source_type=source_type,
                    source_id=source_id,
                    status=status,
                    payload=payload,
                )
                session.add(event)
                await session.commit()
        except Exception as exc:
            logger.warning("persist_live_event_failed", error=str(exc), event_type=event_type)


def emit_live_event(
    event_type: str,
    payload: dict[str, Any],
    source_type: str = "tool",
    source_id: str | None = None,
    status: str = "running",
) -> None:
    """Emit an observable state transition event in real time.

    Events are committed asynchronously and picked up by active SSE streams
    within milliseconds, allowing the UI to reflect live agent actions.
    """
    ctx = _active_run_context.get()
    if not ctx:
        return

    workspace_id = ctx["workspace_id"]
    run_id = ctx["run_id"]

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            _persist_live_event_async(
                workspace_id=workspace_id,
                run_id=run_id,
                event_type=event_type,
                payload=payload,
                source_type=source_type,
                source_id=source_id,
                status=status,
            )
        )
    except RuntimeError:
        pass
