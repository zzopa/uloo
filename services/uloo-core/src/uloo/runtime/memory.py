# AI生成
"""Long-term Memory retrieval and context formatting for Agno Team Coordinator."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.memory import MemoryItem


def _tokenize(text: str) -> set[str]:
    """Extract English words and Chinese n-grams (2-gram to 4-gram) for reliable semantic overlap."""
    cleaned = text.lower().strip()
    tokens: set[str] = set()

    # 1. Alphanumeric words
    for w in re.findall(r"[a-z0-9_-]{2,}", cleaned):
        tokens.add(w)

    # 2. Chinese substrings & n-grams
    chinese_runs = re.findall(r"[\u4e00-\u9fa5]+", cleaned)
    for run in chinese_runs:
        tokens.add(run)
        n = len(run)
        if n >= 2:
            for i in range(n - 1):
                tokens.add(run[i : i + 2])
        if n >= 3:
            for i in range(n - 2):
                tokens.add(run[i : i + 3])
        if n >= 4:
            for i in range(n - 3):
                tokens.add(run[i : i + 4])

    return tokens


async def recall_memories(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    query: str,
    limit: int = 5,
    category: str | None = None,
) -> list[MemoryItem]:
    """Retrieve relevant memories for a task query based on keyword overlap and text matching.

    Args:
        db: Active async database session.
        workspace_id: Tenant workspace scope.
        query: User task query or prompt text.
        limit: Max memory items to return.
        category: Optional category filter.

    Returns:
        List of matching MemoryItem instances ordered by relevance.
    """
    filters = [MemoryItem.workspace_id == workspace_id]
    if category:
        filters.append(MemoryItem.category == category)

    result = await db.execute(select(MemoryItem).where(*filters).order_by(MemoryItem.created_at.desc()).limit(100))
    all_candidates = list(result.scalars().all())
    if not all_candidates:
        return []

    tokens = _tokenize(query)
    if not tokens:
        return all_candidates[:limit]

    scored: list[tuple[float, MemoryItem]] = []
    for item in all_candidates:
        score = 0.0
        # 1. Direct keyword overlap
        kw_list = getattr(item, "keywords", None) or []
        item_keywords = {str(k).lower().strip() for k in kw_list}
        overlap = len(tokens & item_keywords)
        score += overlap * 4.0

        # 2. Content substring match
        content_lower = str(item.content or "").lower()
        for token in tokens:
            if len(token) >= 2 and token in content_lower:
                score += 1.0

        if score > 0:
            scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:limit]]


def format_memories_for_prompt(memories: list[MemoryItem]) -> str:
    """Format recalled memories into a structured section for LLM Coordinator prompt."""
    if not memories:
        return ""

    lines = [
        "### 长期经验与偏好记忆 (Long-Term Memory Recall)",
        "以下为跨任务沉淀的经验、行业事实与用户固定偏好，请优先参考并贯彻到本次规划与协同执行中：",
    ]
    for idx, mem in enumerate(memories, 1):
        category_str = str(mem.category or "fact")
        cat_badge = {"fact": "行业事实", "preference": "用户偏好", "guideline": "执行约定"}.get(
            category_str, category_str
        )
        lines.append(f"{idx}. [{cat_badge}] {mem.content}")

    lines.append("")
    return "\n".join(lines)
