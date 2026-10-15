# AI生成
"""Tests for S4 task constraints, pre-check, and supplement schemas."""

import pytest
from uloo.schemas.task import (
    TaskCreate,
    TaskResponse,
    PreCheckIssue,
    TaskPreCheckResponse,
    TaskSupplementRequest,
    TaskDirectExecuteRequest,
    TaskExportResponse,
)


class TestTaskCreateConstraints:
    def test_create_without_constraints(self):
        tc = TaskCreate(title="Test", query="Do something")
        assert tc.constraints is None

    def test_create_with_constraints(self):
        tc = TaskCreate(title="Test", query="Do something", constraints="Budget under 5000, deadline Jan 30")
        assert tc.constraints == "Budget under 5000, deadline Jan 30"

    def test_constraints_max_10000(self):
        with pytest.raises(ValueError):
            TaskCreate(title="T", query="Q", constraints="x" * 10001)

    def test_constraints_exactly_10000_ok(self):
        tc = TaskCreate(title="T", query="Q", constraints="x" * 10000)
        assert len(tc.constraints) == 10000


class TestPreCheckIssue:
    def test_ok_issue(self):
        issue = PreCheckIssue(category="team", status="ok", detail="1 team available")
        assert issue.status == "ok"
        assert issue.fix_link is None

    def test_missing_with_fix_link(self):
        issue = PreCheckIssue(category="model", status="missing", detail="No agents", fix_link="/uloo/agents")
        assert issue.fix_link == "/uloo/agents"

    def test_invalid_category(self):
        with pytest.raises(ValueError):
            PreCheckIssue(category="invalid", status="ok", detail="bad")

    def test_invalid_status(self):
        with pytest.raises(ValueError):
            PreCheckIssue(category="team", status="invalid", detail="bad")

    def test_detail_max_500(self):
        with pytest.raises(ValueError):
            PreCheckIssue(category="team", status="ok", detail="x" * 501)


class TestTaskPreCheckResponse:
    def test_ready(self):
        resp = TaskPreCheckResponse(ready=True, issues=[])
        assert resp.ready is True
        assert resp.issues == []

    def test_not_ready_with_issues(self):
        issues = [
            PreCheckIssue(category="team", status="missing", detail="No team"),
            PreCheckIssue(category="model", status="ok", detail="Agents ready"),
        ]
        resp = TaskPreCheckResponse(ready=False, issues=issues)
        assert resp.ready is False
        assert len(resp.issues) == 2


class TestTaskSupplementRequest:
    def test_supplements(self):
        req = TaskSupplementRequest(supplements=["Answer 1", "Answer 2"])
        assert len(req.supplements) == 2
        assert req.constraints is None

    def test_supplements_with_constraints(self):
        req = TaskSupplementRequest(supplements=["Answer"], constraints="Additional constraint")
        assert req.constraints == "Additional constraint"

    def test_supplements_min_1(self):
        with pytest.raises(ValueError):
            TaskSupplementRequest(supplements=[])

    def test_supplements_max_20(self):
        with pytest.raises(ValueError):
            TaskSupplementRequest(supplements=[f"s{i}" for i in range(21)])

    def test_constraints_max_10000(self):
        with pytest.raises(ValueError):
            TaskSupplementRequest(supplements=["s"], constraints="x" * 10001)


class TestTaskDirectExecuteRequest:
    def test_default_no_team(self):
        req = TaskDirectExecuteRequest()
        assert req.team_id is None

    def test_with_team_id(self):
        req = TaskDirectExecuteRequest(team_id="abc-123")
        assert req.team_id == "abc-123"


class TestTaskExportResponse:
    def test_markdown_export(self):
        resp = TaskExportResponse(filename="task_test.md", content="# Task\n\nContent")
        assert resp.format == "markdown"
        assert resp.filename == "task_test.md"
        assert "# Task" in resp.content

    def test_empty_content(self):
        resp = TaskExportResponse(filename="empty.md", content="")
        assert resp.content == ""
