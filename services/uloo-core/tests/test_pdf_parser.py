# AI生成
"""Unit tests for PDF parser tool and its runtime integration."""

import json
from pathlib import Path

import pymupdf
import pytest

from uloo.runtime.pdf_parser import _format_table_to_markdown, pdf_parser
from uloo.runtime.resources import configured_pdf_parser
from uloo.runtime.tools import ToolRegistry


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Create a sample 2-page PDF with text, outline, and table."""
    pdf_path = tmp_path / "sample_test.pdf"
    doc = pymupdf.open()

    # Page 1: Headings & Text
    page1 = doc.new_page()
    page1.insert_text((50, 60), "ULOO Architecture Overview", fontsize=16)
    page1.insert_text((50, 100), "ULOO is a multi-agent orchestration runtime with Agno.", fontsize=11)

    # Draw a table with borders on Page 1
    # Outer box
    page1.draw_rect(pymupdf.Rect(50, 140, 350, 240))
    # Horizontal line
    page1.draw_line(pymupdf.Point(50, 180), pymupdf.Point(350, 180))
    # Vertical lines
    page1.draw_line(pymupdf.Point(150, 140), pymupdf.Point(150, 240))
    page1.draw_line(pymupdf.Point(250, 140), pymupdf.Point(250, 240))

    page1.insert_text((60, 165), "Component")
    page1.insert_text((160, 165), "Technology")
    page1.insert_text((260, 165), "Status")

    page1.insert_text((60, 215), "Core Engine")
    page1.insert_text((160, 215), "Agno + FastAPI")
    page1.insert_text((260, 215), "Active")

    # Page 2: Second Section with specific search keyword
    page2 = doc.new_page()
    page2.insert_text((50, 60), "Financial & Benchmark Metrics", fontsize=16)
    page2.insert_text((50, 100), "KeywordTarget: Pine wood average index is 1850 CNY/m3.", fontsize=11)

    # Set metadata and outline (TOC)
    doc.set_toc([
        [1, "Architecture Overview", 1],
        [1, "Financial Metrics", 2],
    ])
    doc.set_metadata({
        "title": "Sample Test Document",
        "author": "ULOO Team",
    })

    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


def test_format_table_to_markdown():
    rows = [
        ["ColA", "ColB | Note"],
        ["Val1\nLine2", "Val2"],
    ]
    md = _format_table_to_markdown(rows)
    assert "| ColA | ColB \\| Note |" in md
    assert "| --- | --- |" in md
    assert "| Val1 Line2 | Val2 |" in md


def test_pdf_parser_missing_file():
    res = json.loads(pdf_parser("non_existent_file_xyz_123.pdf"))
    assert res["status"] == "error"
    assert res["error_type"] == "FileNotFoundError"
    assert "not found" in res["message"]


def test_pdf_parser_invalid_file(tmp_path: Path):
    txt_file = tmp_path / "not_a_pdf.txt"
    txt_file.write_text("Hello, this is just plain text, not a PDF document.")
    res = json.loads(pdf_parser(str(txt_file)))
    assert res["status"] == "error"
    assert res["error_type"] in ("InvalidFormatError", "FileDataError")


def test_pdf_parser_extract_text_and_metadata(sample_pdf: Path):
    res = json.loads(pdf_parser(str(sample_pdf), extract_tables=True))
    assert res["status"] == "ok"
    assert res["file_name"] == "sample_test.pdf"
    assert res["metadata"]["total_pages"] == 2
    assert res["metadata"]["title"] == "Sample Test Document"
    assert len(res["pages"]) == 2

    # Check page 1 text
    page1_text = res["pages"][0]["text"]
    assert "ULOO Architecture Overview" in page1_text

    # Check table extraction
    assert res["tables_count"] >= 1
    page1_tables = res["pages"][0]["tables"]
    assert len(page1_tables) >= 1
    table_md = page1_tables[0]["markdown"]
    assert "Component" in table_md
    assert "Technology" in table_md

    # Check markdown preview
    assert "# Sample Test Document" in res["content_markdown"]
    assert "### 第 1 页" in res["content_markdown"]


def test_pdf_parser_toc_mode(sample_pdf: Path):
    res = json.loads(pdf_parser(str(sample_pdf), mode="toc"))
    assert res["status"] == "ok"
    assert res["metadata"]["total_pages"] == 2
    outline = res["metadata"]["outline"]
    assert len(outline) == 2
    assert outline[0]["title"] == "Architecture Overview"
    assert outline[1]["title"] == "Financial Metrics"


def test_pdf_parser_query_search(sample_pdf: Path):
    res = json.loads(pdf_parser(str(sample_pdf), query="KeywordTarget"))
    assert res["status"] == "ok"
    assert res["matches_count"] >= 1
    assert 2 in res["matching_pages"]
    assert any("KeywordTarget" in match["snippet"] for match in res["results"])


def test_pdf_parser_page_range(sample_pdf: Path):
    # Only parse page 2
    res = json.loads(pdf_parser(str(sample_pdf), page_start=2, page_end=2))
    assert res["status"] == "ok"
    assert len(res["pages"]) == 1
    assert res["pages"][0]["page_number"] == 2
    assert "Financial & Benchmark Metrics" in res["pages"][0]["text"]


def test_tool_registry_resolves_pdf_parser():
    registry = ToolRegistry()
    assert "pdf-parser" in registry.available_refs()
    resolved = registry.resolve_many(["pdf-parser", "web-search"])
    assert len(resolved) == 2
    assert resolved[0] == pdf_parser


def test_configured_pdf_parser_wrapper(sample_pdf: Path):
    item = {
        "key": "pdf-parser",
        "description": "Parse PDF document",
        "config": {"tool_type": "pdf_parser"},
    }
    wrapper = configured_pdf_parser(item)
    assert callable(wrapper)
    out_str = wrapper(str(sample_pdf))
    res = json.loads(out_str)
    assert res["status"] == "ok"
    assert res["metadata"]["total_pages"] == 2


def test_pdf_parser_rejects_parent_directory_traversal():
    res = json.loads(pdf_parser("../../etc/passwd"))
    assert res["status"] == "error"
    assert res["error_type"] == "FileNotFoundError"
