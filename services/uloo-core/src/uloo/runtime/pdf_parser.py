# AI生成
"""PDF parser tool for extracting metadata, page text, and structured tables."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pdfplumber
import pymupdf

logger = logging.getLogger(__name__)


def _format_table_to_markdown(rows: list[list[Any]]) -> str:
    """Format a 2D table grid into a clean GitHub-flavored markdown table."""
    if not rows or not any(rows):
        return ""
    max_cols = max(len(r) for r in rows)
    if max_cols == 0:
        return ""

    clean_rows: list[list[str]] = []
    for row in rows:
        padded = [str(cell if cell is not None else "").replace("\n", " ").replace("|", "\\|").strip() for cell in row]
        if len(padded) < max_cols:
            padded.extend([""] * (max_cols - len(padded)))
        clean_rows.append(padded)

    header = clean_rows[0]
    separator = ["---"] * max_cols
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    for row in clean_rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _resolve_file_path(file_path: str) -> Path | None:
    """Safely resolve file path against cwd, storage, or tmp folders."""
    clean = file_path.strip().replace("\\", "/")
    p = Path(clean)
    if p.is_file():
        return p.resolve()

    cwd = Path.cwd()
    candidates = [
        cwd / p,
        cwd / "storage" / p,
        cwd / "storage" / "uploads" / p,
        cwd / "tmp" / p,
        cwd.parent / p,
        cwd.parent / "storage" / p,
        cwd.parent / "storage" / "uploads" / p,
        cwd.parent / "tmp" / p,
    ]
    for cand in candidates:
        if cand.is_file():
            return cand.resolve()

    # 针对上传附件文件名的容错模糊匹配（如上传文件为 {timestamp}_{hash}_{name}）
    target_name = p.name.lower()
    search_dirs = [
        cwd / "storage" / "uploads",
        cwd / "storage",
        cwd.parent / "storage" / "uploads",
        cwd.parent / "storage",
    ]
    for base_dir in search_dirs:
        if not base_dir.is_dir():
            continue
        # 1. 精确文件名匹配
        for f in base_dir.rglob("*.pdf"):
            if f.name.lower() == target_name:
                return f.resolve()
        # 2. 前缀/后缀模糊匹配 (例如上传生成的哈希前缀)
        for f in base_dir.rglob("*.pdf"):
            if f.name.lower().endswith(target_name) or target_name in f.name.lower():
                return f.resolve()

    return None


def pdf_parser(
    file_path: str,
    page_start: int = 1,
    page_end: int | None = None,
    extract_tables: bool = True,
    query: str = "",
    mode: str = "all",
    max_chars: int = 30000,
) -> str:
    """Parse a PDF document to extract structured text, TOC/metadata, and tables.

    Use this tool to read contracts, financial reports, technical documentation,
    pricing sheets, and extract exact data from tables.

    Args:
        file_path: Path to the target PDF file (e.g. 'storage/report.pdf').
        page_start: 1-indexed starting page number (default 1).
        page_end: 1-indexed ending page number (inclusive). Defaults to page_start + 19.
        extract_tables: Whether to detect and extract tables as markdown & structured rows.
        query: Optional search keyword to filter relevant pages and return snippets.
        mode: Parsing mode - 'all' (text+tables), 'tables' (tables only),
              'text' (text only), or 'toc' (metadata and outline only).
        max_chars: Maximum character limit for returned content (default 30000).

    Returns:
        JSON string containing status, metadata, pages, tables, and markdown representation.
    """
    clean_path_str = file_path.strip()
    if not clean_path_str:
        return json.dumps({"status": "error", "message": "file_path is required"}, ensure_ascii=False)

    resolved_path = _resolve_file_path(clean_path_str)
    if resolved_path is None:
        return json.dumps(
            {
                "status": "error",
                "error_type": "FileNotFoundError",
                "message": f"PDF file not found: '{clean_path_str}'. Please check the file path.",
            },
            ensure_ascii=False,
        )

    # Validate PDF file extension or magic bytes
    if resolved_path.suffix.lower() != ".pdf":
        try:
            with open(resolved_path, "rb") as f:
                header = f.read(5)
                if not header.startswith(b"%PDF-"):
                    return json.dumps(
                        {
                            "status": "error",
                            "error_type": "InvalidFormatError",
                            "message": f"File '{clean_path_str}' is not a valid PDF document.",
                        },
                        ensure_ascii=False,
                    )
        except Exception as exc:  # noqa: BLE001
            return json.dumps(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "message": f"Failed to inspect file '{clean_path_str}': {exc}",
                },
                ensure_ascii=False,
            )

    try:
        doc = pymupdf.open(str(resolved_path))
    except Exception as exc:  # noqa: BLE001
        return json.dumps(
            {
                "status": "error",
                "error_type": type(exc).__name__,
                "message": f"Failed to open PDF document: {exc}",
            },
            ensure_ascii=False,
        )

    try:
        total_pages = doc.page_count
        metadata = {
            "title": doc.metadata.get("title") or resolved_path.stem,
            "author": doc.metadata.get("author") or "",
            "subject": doc.metadata.get("subject") or "",
            "creator": doc.metadata.get("creator") or "",
            "producer": doc.metadata.get("producer") or "",
            "total_pages": total_pages,
        }

        # Extract TOC (Table of Contents / Outline)
        toc_entries = []
        try:
            raw_toc = doc.get_toc()  # [[lvl, title, page], ...]
            for entry in raw_toc:
                if len(entry) >= 3:
                    toc_entries.append({"level": entry[0], "title": entry[1], "page": entry[2]})
        except Exception:  # noqa: BLE001, S110
            pass
        metadata["outline"] = toc_entries

        # Mode 'toc' or 'metadata' returns early
        if mode in ("toc", "metadata"):
            doc.close()
            return json.dumps(
                {
                    "status": "ok",
                    "file_path": str(resolved_path),
                    "file_name": resolved_path.name,
                    "metadata": metadata,
                    "mode": mode,
                },
                ensure_ascii=False,
            )

        # Keyword Search Query Mode
        if query and query.strip():
            search_term = query.strip()
            matches = []
            for pno in range(total_pages):
                page = doc[pno]
                rects = page.search_for(search_term)
                if rects:
                    page_text = page.get_text("text")
                    idx = page_text.lower().find(search_term.lower())
                    if idx != -1:
                        start_pos = max(0, idx - 120)
                        end_pos = min(len(page_text), idx + len(search_term) + 120)
                        snippet = page_text[start_pos:end_pos].replace("\n", " ")
                        if start_pos > 0:
                            snippet = "..." + snippet
                        if end_pos < len(page_text):
                            snippet = snippet + "..."
                    else:
                        snippet = page_text[:200].replace("\n", " ") + "..."

                    matches.append({
                        "page": pno + 1,
                        "occurrences": len(rects),
                        "snippet": snippet,
                    })

            doc.close()
            return json.dumps(
                {
                    "status": "ok",
                    "file_path": str(resolved_path),
                    "file_name": resolved_path.name,
                    "metadata": metadata,
                    "query": search_term,
                    "matches_count": len(matches),
                    "matching_pages": [m["page"] for m in matches],
                    "results": matches[:20],
                },
                ensure_ascii=False,
            )

        # Normal extraction: clamp page range
        p_start = max(1, page_start)
        if page_end is None:
            p_end = min(total_pages, p_start + 19)
        else:
            p_end = min(total_pages, max(p_start, page_end))

        plumber_doc = None
        if extract_tables and mode in ("all", "tables"):
            try:
                plumber_doc = pdfplumber.open(str(resolved_path))
            except Exception:  # noqa: BLE001
                plumber_doc = None

        pages_data: list[dict[str, Any]] = []
        md_sections: list[str] = [
            f"# {metadata['title'] or resolved_path.stem}",
            f"- 文件名: `{resolved_path.name}`",
            f"- 总页数: {total_pages} 页 | 当前解析: 第 {p_start} 至 {p_end} 页",
        ]
        if toc_entries:
            md_sections.append("### 文档目录 (TOC 摘要)")
            for entry in toc_entries[:10]:
                indent = "  " * (entry["level"] - 1)
                md_sections.append(f"{indent}- {entry['title']} (P.{entry['page']})")
        md_sections.append("\n---")

        total_chars = 0
        total_tables = 0

        for p_num in range(p_start, p_end + 1):
            if total_chars >= max_chars:
                md_sections.append(f"\n> [!NOTE] 已达到最大字符截断限制 ({max_chars} 字符)，后续页面已截断。")
                break

            p_idx = p_num - 1
            pymupdf_page = doc[p_idx]
            page_text = pymupdf_page.get_text("text").strip() if mode != "tables" else ""

            page_tables: list[dict[str, Any]] = []
            if extract_tables and mode in ("all", "tables"):
                raw_tables: list[list[list[Any]]] = []
                if plumber_doc and p_idx < len(plumber_doc.pages):
                    try:
                        raw_tables = plumber_doc.pages[p_idx].extract_tables() or []
                    except Exception:  # noqa: BLE001
                        raw_tables = []

                if not raw_tables:
                    try:
                        mu_tables = pymupdf_page.find_tables()
                        if mu_tables:
                            raw_tables = [tab.extract() for tab in mu_tables]
                    except Exception:  # noqa: BLE001, S110
                        pass

                for t_idx, raw_table in enumerate(raw_tables, 1):
                    md_tab = _format_table_to_markdown(raw_table)
                    if md_tab:
                        page_tables.append({
                            "table_index": t_idx,
                            "markdown": md_tab,
                            "rows": raw_table,
                        })
                        total_tables += 1

            page_dict = {
                "page_number": p_num,
                "text": page_text,
                "tables": page_tables,
            }
            pages_data.append(page_dict)

            page_md_lines = [f"\n### 第 {p_num} 页"]
            if page_text:
                page_md_lines.append(page_text)
            if page_tables:
                page_md_lines.append("\n**[提取表格]**")
                for pt in page_tables:
                    page_md_lines.append(pt["markdown"])
            page_snippet = "\n\n".join(page_md_lines)
            md_sections.append(page_snippet)
            total_chars += len(page_snippet)

        if plumber_doc:
            plumber_doc.close()
        doc.close()

        full_md = "\n".join(md_sections)
        if len(full_md) > max_chars:
            full_md = full_md[:max_chars] + "\n\n...[已达返回长度上限截断]..."

        return json.dumps(
            {
                "status": "ok",
                "file_path": str(resolved_path),
                "file_name": resolved_path.name,
                "metadata": metadata,
                "pages_analyzed": [p_start, min(p_end, p_start + len(pages_data) - 1)],
                "tables_count": total_tables,
                "total_characters": total_chars,
                "pages": pages_data,
                "content_markdown": full_md,
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.exception("Error parsing PDF %s", clean_path_str)
        try:
            if not doc.is_closed:
                doc.close()
        except Exception:  # noqa: BLE001, S110
            pass
        return json.dumps(
            {
                "status": "error",
                "error_type": type(exc).__name__,
                "message": f"Exception occurred while parsing PDF: {exc}",
            },
            ensure_ascii=False,
        )
