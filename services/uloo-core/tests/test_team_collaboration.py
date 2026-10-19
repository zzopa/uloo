# AI生成
"""Tests for S3 team collaboration fields: review_policy, completion, handoff_context, total_token_budget."""

import uuid

import pytest

from uloo.schemas.team import (
    CompletionConditions,
    ReviewPolicy,
    TeamCreate,
    TeamLimits,
    TeamUpdate,
)

VALID_AGENT_1 = uuid.uuid4()
VALID_AGENT_2 = uuid.uuid4()


class TestReviewPolicy:
    def test_defaults(self):
        rp = ReviewPolicy()
        assert rp.max_rounds == 3
        assert rp.require_unanimous is False
        assert rp.criteria == []

    def test_full_config(self):
        rp = ReviewPolicy(max_rounds=5, require_unanimous=True, criteria=["accuracy", "completeness"])
        assert rp.max_rounds == 5
        assert rp.require_unanimous is True
        assert rp.criteria == ["accuracy", "completeness"]

    def test_max_rounds_bounds(self):
        with pytest.raises(ValueError):
            ReviewPolicy(max_rounds=0)
        with pytest.raises(ValueError):
            ReviewPolicy(max_rounds=11)

    def test_criteria_max_20(self):
        with pytest.raises(ValueError):
            ReviewPolicy(criteria=[f"c{i}" for i in range(21)])

    def test_criteria_exactly_20_ok(self):
        rp = ReviewPolicy(criteria=[f"c{i}" for i in range(20)])
        assert len(rp.criteria) == 20


class TestCompletionConditions:
    def test_defaults(self):
        cc = CompletionConditions()
        assert cc.all_members_executed is True
        assert cc.no_unresolved_errors is True
        assert cc.custom == ""

    def test_full_config(self):
        cc = CompletionConditions(all_members_executed=False, no_unresolved_errors=False, custom="All reports submitted")
        assert cc.all_members_executed is False
        assert cc.no_unresolved_errors is False
        assert cc.custom == "All reports submitted"

    def test_custom_max_2000(self):
        with pytest.raises(ValueError):
            CompletionConditions(custom="x" * 2001)

    def test_custom_exactly_2000_ok(self):
        cc = CompletionConditions(custom="x" * 2000)
        assert len(cc.custom) == 2000


class TestTeamLimitsTokenBudget:
    def test_total_token_budget_default(self):
        limits = TeamLimits()
        assert limits.total_token_budget == 200000

    def test_total_token_budget_set(self):
        limits = TeamLimits(total_token_budget=500000)
        assert limits.total_token_budget == 500000

    def test_total_token_budget_min_1(self):
        with pytest.raises(ValueError):
            TeamLimits(total_token_budget=0)

    def test_total_token_budget_max(self):
        with pytest.raises(ValueError):
            TeamLimits(total_token_budget=10_000_001)


class TestTeamCreateWithCollaboration:
    def test_create_with_collaboration_fields(self):
        team = TeamCreate(
            key="research-team",
            name="Research Team",
            mode="coordinate",
            member_agent_ids=[VALID_AGENT_1, VALID_AGENT_2],
            leader_agent_id=VALID_AGENT_1,
            review_policy=ReviewPolicy(max_rounds=5, require_unanimous=True, criteria=["accuracy"]),
            completion=CompletionConditions(custom="All deliverables reviewed"),
            handoff_context="Previous session context: market analysis completed",
        )
        assert team.review_policy.max_rounds == 5
        assert team.review_policy.require_unanimous is True
        assert team.completion.custom == "All deliverables reviewed"
        assert team.handoff_context == "Previous session context: market analysis completed"

    def test_create_defaults(self):
        team = TeamCreate(
            key="simple-team",
            name="Simple Team",
            mode="coordinate",
            member_agent_ids=[VALID_AGENT_1],
            leader_agent_id=VALID_AGENT_1,
        )
        assert team.review_policy.max_rounds == 3
        assert team.completion.all_members_executed is True
        assert team.handoff_context == ""

    def test_handoff_context_max_2000(self):
        with pytest.raises(ValueError):
            TeamCreate(
                key="t",
                name="T",
                mode="coordinate",
                member_agent_ids=[VALID_AGENT_1],
                leader_agent_id=VALID_AGENT_1,
                handoff_context="x" * 2001,
            )


class TestTeamUpdateWithCollaboration:
    def test_update_partial_collaboration(self):
        update = TeamUpdate(
            expected_version=1,
            review_policy=ReviewPolicy(max_rounds=7),
        )
        assert update.review_policy is not None
        assert update.review_policy.max_rounds == 7
        assert update.completion is None
        assert update.handoff_context is None

    def test_update_all_collaboration_fields(self):
        update = TeamUpdate(
            expected_version=2,
            review_policy=ReviewPolicy(max_rounds=2, require_unanimous=True),
            completion=CompletionConditions(all_members_executed=False),
            handoff_context="Updated context",
        )
        assert update.review_policy.max_rounds == 2
        assert update.completion.all_members_executed is False
        assert update.handoff_context == "Updated context"
