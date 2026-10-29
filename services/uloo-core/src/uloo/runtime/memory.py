# AI生成
"""Long-term Memory retrieval, consolidation, and context formatting for Agno Team Coordinator."""

from __future__ import annotations

import asyncio
import json
import re
import uuid

from agno.agent import Agent
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..logging import get_logger
from ..models.memory import MemoryItem
from .models import ModelRegistry

logger = get_logger(__name__)


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
        "### 长期经验与偏好记忆 (Long-Term Memory & LLM-Wiki Recall)",
        "以下为跨任务沉淀的经验、行业事实与用户固定偏好，请优先参考并贯彻到本次规划与协同执行中：",
    ]
    for idx, mem in enumerate(memories, 1):
        category_str = str(mem.category or "fact")
        cat_badge = {"fact": "行业事实", "preference": "用户偏好", "guideline": "执行约定"}.get(
            category_str, category_str
        )
        tag_str = f" [标签: {', '.join(mem.keywords)}]" if mem.keywords else ""
        lines.append(f"{idx}. [{cat_badge}] {mem.content}{tag_str}")

    lines.append("")
    return "\n".join(lines)


class DistilledMemoryItem(BaseModel):
    category: str = Field(default="fact", description="One of: fact, preference, guideline")
    content: str = Field(..., description="精炼的实体事实、结论或偏好描述")
    keywords: list[str] = Field(default_factory=list, description="实体与关键标签，如['企业名', '人名']")


class DistilledMemoriesDraft(BaseModel):
    memories: list[DistilledMemoryItem] = Field(default_factory=list)


async def distill_run_memories(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    query: str,
    deliverable_text: str,
    models: ModelRegistry | None = None,
) -> list[MemoryItem]:
    """Distill structured Wiki entity memories from a completed task deliverable using LLM."""
    clean_text = deliverable_text.strip()
    if len(clean_text) < 50:
        return []

    planner_ref = settings.planner_model_ref.strip()
    if not planner_ref:
        if settings.llm_base_url.strip() and settings.llm_model.strip():
            planner_ref = f"default:{settings.llm_model.strip()}"
        else:
            planner_ref = "default:agnes-3.0-flash"

    model_registry = models or ModelRegistry(workspace_id=str(workspace_id))
    try:
        model = model_registry.resolve(planner_ref)
    except Exception as exc:
        logger.warning("wiki_distiller_model_resolve_failed", error=str(exc))
        return []

    distiller = Agent(
        agent_id="uloo-wiki-distiller",
        name="ULOO Wiki Curator",
        model=model,
        response_model=DistilledMemoriesDraft,
        parse_response=True,
        structured_outputs=False,
        use_json_mode=True,
        instructions=[
            "你是个人与企业级 LLM-Wiki 演进知识库的专职档案管理员 (Wiki Curator)。",
            "请阅读用户本次任务与最终交付研报，提炼出 1 到 3 条最关键、具有长期沉淀价值的【实体事实/核心结论 Wiki 词条】（例如：特定公司主营业务与法人、特定人物核心资质与背景、关键技术指标或政策裁定）。",
            "每条 content 必须语言精炼（1~2 句话）、客观明确且包含主体名称（例如：'龚杰为武汉华信联创公司的法定代表人...'），严禁空洞套话。",
            "每条 keywords 必须提取 2~4 个高价值实体或属性词（例如：['武汉华信联创', '龚杰', '企业背景']）。",
            "category 可选：'fact'（客观实体事实）、'preference'（用户固定偏好/要求）、'guideline'（规程或判定准则）。",
            "若研报内容空洞或无具体实体事实，返回空列表。",
        ],
    )

    prompt = f"【用户任务】:\n{query}\n\n【本次交付研报核心内容】:\n{clean_text[:4000]}"
    try:
        res = await asyncio.wait_for(distiller.arun(prompt, stream=False), timeout=45.0)
        draft: DistilledMemoriesDraft | None = None
        if hasattr(res, "content") and isinstance(res.content, DistilledMemoriesDraft):
            draft = res.content
        elif hasattr(res, "content") and isinstance(res.content, dict):
            draft = DistilledMemoriesDraft.model_validate(res.content)
        elif hasattr(res, "content") and isinstance(res.content, str):
            cleaned = res.content.strip()
            m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", cleaned, flags=re.DOTALL)
            json_str = m.group(1).strip() if m else cleaned
            try:
                parsed = json.loads(json_str)
                draft = DistilledMemoriesDraft.model_validate(parsed)
            except Exception:
                brace_match = re.search(r"(\{.*\})", cleaned, flags=re.DOTALL)
                if brace_match:
                    draft = DistilledMemoriesDraft.model_validate(json.loads(brace_match.group(1)))

        if not draft or not draft.memories:
            return []

        saved_items: list[MemoryItem] = []
        for item in draft.memories[:3]:
            text_val = (item.content or "").strip()
            if len(text_val) < 5:
                continue
            cat = item.category if item.category in {"fact", "preference", "guideline"} else "fact"
            kw = [str(k).strip() for k in (item.keywords or []) if str(k).strip()]
            mem_item = MemoryItem(
                workspace_id=workspace_id,
                category=cat,
                content=text_val,
                keywords=kw,
                source_run_id=run_id,
            )
            db.add(mem_item)
            saved_items.append(mem_item)

        if saved_items:
            await db.commit()
            for it in saved_items:
                await db.refresh(it)
            logger.info("wiki_memories_distilled_success", count=len(saved_items), run_id=str(run_id))
        return saved_items
    except Exception as exc:
        logger.warning("wiki_distillation_error", run_id=str(run_id), error=str(exc))
        return []
