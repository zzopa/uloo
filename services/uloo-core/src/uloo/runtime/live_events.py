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
# Track pending in-flight persistence tasks per run_id
_pending_tasks: dict[str, set[asyncio.Task]] = {}

# Reference to the main asyncio event loop to allow worker threads (e.g. sync tools in threadpool)
# to safely schedule event persistence coroutines
_main_event_loop: asyncio.AbstractEventLoop | None = None


def get_run_sequence_lock(run_id: str | uuid.UUID) -> asyncio.Lock:
    """Return the sequential lock for a run."""
    run_id_str = str(run_id)
    if run_id_str not in _run_sequence_locks:
        _run_sequence_locks[run_id_str] = asyncio.Lock()
    return _run_sequence_locks[run_id_str]


async def wait_pending_live_events(run_id: str | uuid.UUID, timeout: float = 10.0) -> None:
    """Wait for all outstanding live event persistence tasks to commit."""
    run_id_str = str(run_id)
    tasks = list(_pending_tasks.get(run_id_str, set()))
    if tasks:
        try:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=timeout)
        except Exception as exc:
            logger.warning("wait_pending_live_events_error", error=str(exc))
        _pending_tasks.pop(run_id_str, None)


def set_active_run(run_id: str | uuid.UUID, workspace_id: str | uuid.UUID) -> None:
    """Set the active run context for real-time live event streaming."""
    global _main_event_loop
    try:
        _main_event_loop = asyncio.get_running_loop()
    except RuntimeError:
        pass

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
    lock = get_run_sequence_lock(run_id)

    async with lock:
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
                logger.info(
                    "live_event_committed",
                    seq=seq,
                    run_id=str(run_id),
                    event_type=event_type,
                    source_id=source_id,
                )
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
    Supports being called from both main asyncio coroutines and worker threads.
    """
    ctx = _active_run_context.get()
    if not ctx:
        return

    workspace_id = ctx["workspace_id"]
    run_id = ctx["run_id"]
    run_id_str = str(run_id)

    coro = _persist_live_event_async(
        workspace_id=workspace_id,
        run_id=run_id,
        event_type=event_type,
        payload=payload,
        source_type=source_type,
        source_id=source_id,
        status=status,
    )

    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(coro)
        if run_id_str not in _pending_tasks:
            _pending_tasks[run_id_str] = set()
        _pending_tasks[run_id_str].add(task)
        task.add_done_callback(lambda t: _pending_tasks.get(run_id_str, set()).discard(t))
    except RuntimeError:
        # Worker thread (e.g. synchronous tool execution via asyncio.to_thread)
        if _main_event_loop and _main_event_loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, _main_event_loop)
        else:
            logger.warning("no_event_loop_for_live_event", event_type=event_type)


def cleanup_run_state(run_id: str | uuid.UUID) -> None:
    """Release per-run lock and pending task references to prevent memory leaks."""
    run_id_str = str(run_id)
    _run_sequence_locks.pop(run_id_str, None)
    _pending_tasks.pop(run_id_str, None)
