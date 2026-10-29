# AI生成
"""Pydantic schemas for Long-term Memory API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MemoryCategory = Literal["fact", "preference", "guideline"]


class MemoryCreate(BaseModel):
    """Payload to record a new long-term memory."""

    category: MemoryCategory = Field("fact", description="Memory type: fact, preference, or guideline")
    content: str = Field(..., min_length=1, max_length=10000, description="Durable memory content")
    keywords: list[str] = Field(default_factory=list, description="Keywords for fast semantic recall")
    source_run_id: uuid.UUID | None = Field(None, description="Optional Task Run ID that consolidated this memory")


class MemoryResponse(BaseModel):
    """Full detail of a memory item."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    category: str
    content: str
    keywords: list[str]
    has_embedding: bool = False
    source_run_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class MemoryListResponse(BaseModel):
    """Paginated list of memories."""

    items: list[MemoryResponse]
    total: int
    offset: int
    limit: int


class MemoryRecallResponse(BaseModel):
    """Top matched memories recalled for a specific query."""

    query: str
    recalled: list[MemoryResponse]
    formatted_context: str
