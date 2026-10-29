# AI生成
"""Long-term Memory APIs for cross-task facts and preference retention."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import transaction_session
from ..errors import ApiError
from ..models.memory import MemoryItem
from ..runtime.embeddings import get_text_embedding_async
from ..runtime.memory import format_memories_for_prompt, recall_memories
from ..schemas.memory import (
    MemoryCreate,
    MemoryListResponse,
    MemoryRecallResponse,
    MemoryResponse,
)
from ..security import RequestContext, require_service_context

router = APIRouter(tags=["memories"])


@router.post("/memories", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
async def create_memory(
    body: MemoryCreate,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Record a durable memory item (fact, preference, or guideline) in the workspace."""
    clean_content = body.content.strip()
    embedding_vec = await get_text_embedding_async(f"{body.category}: {clean_content}")

    item = MemoryItem(
        workspace_id=context.workspace_id,
        category=body.category,
        content=clean_content,
        keywords=body.keywords or [],
        source_run_id=body.source_run_id,
        embedding=embedding_vec,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return MemoryResponse.model_validate(item.to_dict())


@router.post("/memories/backfill-embeddings")
async def backfill_memory_embeddings(
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Backfill missing pgvector embeddings for memories in this workspace."""
    rows = await db.scalars(
        select(MemoryItem).where(
            MemoryItem.workspace_id == context.workspace_id,
            MemoryItem.embedding.is_(None),
        )
    )
    items = list(rows)
    updated_count = 0
    for item in items:
        vec = await get_text_embedding_async(f"{item.category}: {item.content}")
        if vec:
            item.embedding = vec
            updated_count += 1
    if updated_count > 0:
        await db.commit()
    return {
        "total_scanned": len(items),
        "updated": updated_count,
        "message": f"Successfully backfilled embeddings for {updated_count}/{len(items)} memory items.",
    }


@router.get("/memories", response_model=MemoryListResponse)
async def list_memories(
    category: str | None = Query(None, description="Filter by fact, preference, or guideline"),
    query: str | None = Query(None, description="Optional search keywords"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """List durable memories in the current workspace with optional keyword filtering."""
    if query:
        items = await recall_memories(db, context.workspace_id, query=query, limit=limit, category=category)
        total = len(items)
        return MemoryListResponse(
            items=[MemoryResponse.model_validate(m.to_dict()) for m in items],
            total=total,
            offset=offset,
            limit=limit,
        )

    filters = [MemoryItem.workspace_id == context.workspace_id]
    if category:
        filters.append(MemoryItem.category == category)

    count_val = await db.scalar(select(func.count()).select_from(MemoryItem).where(*filters))
    total = int(count_val or 0)
    rows = await db.execute(
        select(MemoryItem)
        .where(*filters)
        .order_by(MemoryItem.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return MemoryListResponse(
        items=[MemoryResponse.model_validate(m.to_dict()) for m in rows.scalars().all()],
        total=total or 0,
        offset=offset,
        limit=limit,
    )


@router.get("/memories/recall", response_model=MemoryRecallResponse)
async def recall_memories_endpoint(
    query: str = Query(..., min_length=1, description="Task query to recall relevant memories for"),
    limit: int = Query(5, ge=1, le=20),
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Recall relevant facts and preferences for a task query, returning formatted prompt context."""
    recalled = await recall_memories(db, context.workspace_id, query=query, limit=limit)
    formatted = format_memories_for_prompt(recalled)
    return MemoryRecallResponse(
        query=query,
        recalled=[MemoryResponse.model_validate(m.to_dict()) for m in recalled],
        formatted_context=formatted,
    )


@router.get("/memories/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    memory_id: uuid.UUID,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Retrieve a single memory item by ID."""
    row = await db.scalar(
        select(MemoryItem).where(
            MemoryItem.id == memory_id,
            MemoryItem.workspace_id == context.workspace_id,
        )
    )
    if not row:
        raise ApiError(status.HTTP_404_NOT_FOUND, "MEMORY_NOT_FOUND", "Memory item not found")
    return MemoryResponse.model_validate(row.to_dict())


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: uuid.UUID,
    context: RequestContext = Depends(require_service_context),
    db: AsyncSession = transaction_session,
):
    """Delete a memory item."""
    row = await db.scalar(
        select(MemoryItem).where(
            MemoryItem.id == memory_id,
            MemoryItem.workspace_id == context.workspace_id,
        )
    )
    if not row:
        raise ApiError(status.HTTP_404_NOT_FOUND, "MEMORY_NOT_FOUND", "Memory item not found")
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
