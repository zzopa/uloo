# AI生成
"""Long-term Memory retrieval, consolidation, and context formatting for Agno Team Coordinator."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
import uuid

from agno.agent import Agent
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ..config import settings
from ..logging import get_logger
from ..models.memory import MemoryItem
from .live_events import emit_live_event
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


_tool_engine = None
_tool_session_factory = None


def _get_tool_session_factory() -> async_sessionmaker[AsyncSession]:
    """Provide a thread-safe connection pool with NullPool so worker threads don't collide on asyncpg loops."""
    global _tool_engine, _tool_session_factory
    if _tool_session_factory is None:
        _tool_engine = create_async_engine(
            settings.database_url,
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": settings.db_schema}},
        )
        _tool_session_factory = async_sessionmaker(
            _tool_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _tool_session_factory


def _run_async_in_thread(coro):
    """Run an async coroutine safely inside a worker thread, preventing event loop collisions."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()


async def _resolve_workspace_uuid(workspace_id: str | uuid.UUID | None, db: AsyncSession) -> uuid.UUID:
    """Resolve workspace UUID from explicit parameter, context, or database fallback."""
    if workspace_id:
        if isinstance(workspace_id, uuid.UUID):
            return workspace_id
        try:
            return uuid.UUID(str(workspace_id).strip())
        except (ValueError, TypeError):
            pass

    existing_ws = await db.scalar(select(MemoryItem.workspace_id).limit(1))
    if existing_ws:
        return existing_ws

    return uuid.UUID("39dc2f4f-2dc3-4cc5-af5f-daad8ee83b6b")


def wiki_memory(
    action: str,
    query: str = "",
    content: str = "",
    category: str = "fact",
    keywords: str = "",
    workspace_id: str | uuid.UUID | None = None,
) -> str:
    """Query or persist entity facts, user preferences, and execution guidelines in the workspace Long-Term Memory (LLM-Wiki).

    Args:
        action: Either 'query' (search historical memories) or 'save' (persist a new confirmed memory entry).
        query: Search keywords or topic for action='query' (e.g. '张三 行业资质' or '报告排版偏好').
        content: Memory content to persist for action='save' (must be substantive, 1-2 concise sentences containing subject name).
        category: Category of memory: 'fact' (objective entity fact), 'preference' (user style/decision preference), or 'guideline' (procedural rule).
        keywords: Comma-separated or space-separated key entity tags for action='save' (e.g. '张三, 资质评审, 企业背景').
        workspace_id: Tenant workspace scope (injected automatically).

    Returns:
        Structured result string detailing recalled facts or confirmation of persistence.
    """
    act = (action or "").strip().lower()

    if act == "query":
        clean_query = query.strip()
        if not clean_query:
            return "提示: 请提供具体的查询关键词 (query)，例如: wiki_memory(action='query', query='目标实体或概念')。"

        emit_live_event(
            event_type="tool.calling",
            source_type="tool",
            source_id="wiki-memory",
            payload={
                "tool_name": "wiki-memory",
                "action": "query",
                "query": clean_query,
                "message": f"正在检索长期记忆中枢 (LLM-Wiki): {clean_query}",
            },
        )

        async def _exec_query() -> list[MemoryItem]:
            factory = _get_tool_session_factory()
            async with factory() as session:
                ws_uuid = await _resolve_workspace_uuid(workspace_id, session)
                cat_filter = category.strip().lower() if category.strip().lower() in {"fact", "preference", "guideline"} else None
                return await recall_memories(session, ws_uuid, query=clean_query, limit=5, category=cat_filter)

        try:
            items = _run_async_in_thread(_exec_query())
        except Exception as exc:
            logger.warning("wiki_memory_query_failed", query=clean_query, error=str(exc))
            emit_live_event(
                event_type="tool.summary",
                source_type="tool",
                source_id="wiki-memory",
                payload={
                    "tool_name": "wiki-memory",
                    "action": "query",
                    "query": clean_query,
                    "status": "error",
                    "result_preview": f"查询失败: {exc}",
                },
            )
            return f"检索长期记忆失败: {exc}"

        preview = f"查得 {len(items)} 条匹配记忆词条" if items else "未找到相关记忆词条"
        emit_live_event(
            event_type="tool.summary",
            source_type="tool",
            source_id="wiki-memory",
            payload={
                "tool_name": "wiki-memory",
                "action": "query",
                "query": clean_query,
                "count": len(items),
                "status": "ok",
                "result_preview": preview,
                "sources": [
                    {"category": it.category, "content": it.content, "keywords": it.keywords}
                    for it in items[:3]
                ],
            },
        )

        lines = [f"【长期记忆中枢 (LLM-Wiki) 检索结果 - 关键词: {clean_query}】"]
        if not items:
            lines.append("未检索到相关历史实体事实或偏好词条。请基于当前最新信源开展专业推演。")
        else:
            for idx, item in enumerate(items, 1):
                cat_badge = {"fact": "行业事实", "preference": "用户偏好", "guideline": "执行约定"}.get(
                    item.category, item.category
                )
                tag_str = f" [标签: {', '.join(item.keywords)}]" if item.keywords else ""
                lines.append(f"{idx}. [{cat_badge}] {item.content}{tag_str}")
        return "\n".join(lines)

    elif act == "save":
        clean_content = content.strip()
        if len(clean_content) < 5:
            return "保存失败: 记忆词条内容 (content) 必须是具有实质价值的事实或偏好描述，长度至少 5 个字符。"

        cat = category.strip().lower()
        if cat not in {"fact", "preference", "guideline"}:
            cat = "fact"

        kw_list: list[str] = []
        if isinstance(keywords, list):
            kw_list = [str(k).strip() for k in keywords if str(k).strip()]
        elif isinstance(keywords, str) and keywords.strip():
            parts = re.split(r"[,;，；\s]+", keywords.strip())
            kw_list = [p for p in parts if p]
        if not kw_list:
            kw_list = list(_tokenize(clean_content))[:4]

        emit_live_event(
            event_type="tool.calling",
            source_type="tool",
            source_id="wiki-memory",
            payload={
                "tool_name": "wiki-memory",
                "action": "save",
                "category": cat,
                "content": clean_content[:120],
                "keywords": kw_list,
                "message": f"正在向长期记忆中枢写入 [{cat}] 词条: {clean_content[:80]}...",
            },
        )

        async def _exec_save() -> MemoryItem:
            factory = _get_tool_session_factory()
            async with factory() as session:
                ws_uuid = await _resolve_workspace_uuid(workspace_id, session)
                mem_item = MemoryItem(
                    workspace_id=ws_uuid,
                    category=cat,
                    content=clean_content,
                    keywords=kw_list,
                )
                session.add(mem_item)
                await session.commit()
                await session.refresh(mem_item)
                return mem_item

        try:
            saved_item = _run_async_in_thread(_exec_save())
        except Exception as exc:
            logger.warning("wiki_memory_save_failed", error=str(exc))
            emit_live_event(
                event_type="tool.summary",
                source_type="tool",
                source_id="wiki-memory",
                payload={
                    "tool_name": "wiki-memory",
                    "action": "save",
                    "status": "error",
                    "result_preview": f"持久化失败: {exc}",
                },
            )
            return f"沉淀长期记忆失败: {exc}"

        emit_live_event(
            event_type="tool.summary",
            source_type="tool",
            source_id="wiki-memory",
            payload={
                "tool_name": "wiki-memory",
                "action": "save",
                "category": cat,
                "id": str(saved_item.id),
                "status": "ok",
                "result_preview": f"成功持久化记忆词条: {saved_item.content[:100]}",
            },
        )

        return (
            f"【长期记忆中枢 (LLM-Wiki) 持久化成功】\n"
            f"- 词条类别: {cat}\n"
            f"- 核心内容: {saved_item.content}\n"
            f"- 标签索引: {', '.join(saved_item.keywords)}\n"
            f"- 记忆ID: {saved_item.id}"
        )

    else:
        return f"不支持的操作 '{action}'。wiki_memory 仅支持 action='query' (检索记忆) 或 action='save' (沉淀记忆)。"
