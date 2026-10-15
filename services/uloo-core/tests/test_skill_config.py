# AI生成
"""Tests for the enhanced SkillConfig schema (S2: 智能体岗位与技能)."""

import pytest
from uloo.schemas.resource import SkillConfig


class TestSkillConfigEnhanced:
    """Verify the new structured fields on SkillConfig."""

    def test_minimal_config_still_works(self):
        """Existing skills with only instructions + tool_refs must still validate."""
        cfg = SkillConfig(
            instructions=["You are a researcher."],
            tool_refs=["web-search"],
        )
        assert cfg.scenarios == ""
        assert cfg.steps == []
        assert cfg.result_format == ""
        assert cfg.quality_checks == []

    def test_full_config_with_all_new_fields(self):
        """A skill with all new fields should validate."""
        cfg = SkillConfig(
            instructions=["Research the topic thoroughly."],
            tool_refs=["web-search"],
            scenarios="Use when the user asks for price comparison or market research.",
            steps=[
                "Identify target products and specifications.",
                "Search multiple sources for current prices.",
                "Cross-verify prices with at least two independent sources.",
                "Format results with source, price, date, and confidence.",
            ],
            result_format="Markdown table with columns: Product, Price, Source, Date, Confidence.",
            quality_checks=[
                "All prices include currency and unit.",
                "Each price has at least one source URL.",
                "Prices are from the last 30 days.",
            ],
        )
        assert cfg.scenarios.startswith("Use when")
        assert len(cfg.steps) == 4
        assert "Markdown table" in cfg.result_format
        assert len(cfg.quality_checks) == 3

    def test_extra_fields_rejected(self):
        """No arbitrary fields allowed — closed schema."""
        with pytest.raises(Exception):
            SkillConfig(
                instructions=["test"],
                unknown_field="should fail",
            )

    def test_steps_max_length_enforced(self):
        """At most 20 work steps."""
        with pytest.raises(Exception):
            SkillConfig(
                instructions=["test"],
                steps=[f"Step {i}" for i in range(21)],
            )

    def test_quality_checks_max_length_enforced(self):
        """At most 20 quality checks."""
        with pytest.raises(Exception):
            SkillConfig(
                instructions=["test"],
                quality_checks=[f"Check {i}" for i in range(21)],
            )

    def test_scenarios_max_length_enforced(self):
        """Scenarios description capped at 2000 chars."""
        with pytest.raises(Exception):
            SkillConfig(
                instructions=["test"],
                scenarios="x" * 2001,
            )

    def test_result_format_max_length_enforced(self):
        """Result format capped at 2000 chars."""
        with pytest.raises(Exception):
            SkillConfig(
                instructions=["test"],
                result_format="x" * 2001,
            )

    def test_empty_steps_and_quality_checks_allowed(self):
        """Empty lists are valid — fields are optional."""
        cfg = SkillConfig(
            instructions=["test"],
            steps=[],
            quality_checks=[],
        )
        assert cfg.steps == []
        assert cfg.quality_checks == []
