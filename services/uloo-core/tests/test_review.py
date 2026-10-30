# AI生成
"""Tests for S3-补1 and S3-补3: post-execution review and completion-condition evaluation."""


import pytest

from uloo.runtime.execution import AgentExecutionResult
from uloo.runtime.review import (
    CompletionEvaluation,
    build_review_feedback,
    evaluate_completion,
)


def _result(
    output: dict | None = None,
    member_results: list[dict] | None = None,
) -> AgentExecutionResult:
    return AgentExecutionResult(
        run_id="test-run",
        output=output or {"content": "task completed with sufficient detail and sources cited"},
        usage={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        member_results=member_results or [],
    )


class TestEvaluateCompletion:
    """Completion condition evaluation against execution results."""

    @pytest.mark.parametrize('output,valid', [
        ({'answer': 19, 'check': '12 + 7 = 19'}, True),
        ({'answer': 0}, True),
        ({'answer': False}, True),
        ({}, False),
        ({'items': []}, False),
        ({'answer': None}, False),
        ({'error': True, 'answer': 19}, False),
    ])
    def test_structured_results_are_not_mistaken_for_empty_text(self, output, valid):
        result = _result(output=output, member_results=[{'agent_id': 'analyst', 'output': output}])
        evaluation = evaluate_completion(
            result, assigned_agent_ids={'analyst'}, completion_config={'no_unresolved_errors': True},
            review_policy={'max_rounds': 3, 'criteria': ['Return a verified result']},
        )
        assert evaluation.satisfied is valid

    def test_all_conditions_met(self):
        """All members executed, no errors, non-empty output → satisfied."""
        result = _result(
            member_results=[
                {"agent_id": "agent-1", "name": "Researcher", "output": {"content": "found data from multiple sources [1] [2]"}, "usage": None},
                {"agent_id": "agent-2", "name": "Writer", "output": {"content": "report written based on research findings and cited references"}, "usage": None},
            ],
        )
        evaluation = evaluate_completion(
            result,
            assigned_agent_ids={"agent-1", "agent-2"},
            completion_config={"all_members_executed": True, "no_unresolved_errors": True},
            review_policy={},
        )
        assert evaluation.satisfied
        assert evaluation.reasons == []
        assert evaluation.missing_members == []
        assert evaluation.error_members == []

    def test_missing_member_not_executed(self):
        """A member that was assigned but didn't execute → not satisfied."""
        result = _result(
            member_results=[
                {"agent_id": "agent-1", "name": "Researcher", "output": {"content": "found data from sources [1]"}, "usage": None},
            ],
        )
        evaluation = evaluate_completion(
            result,
            assigned_agent_ids={"agent-1", "agent-2"},
            completion_config={"all_members_executed": True, "no_unresolved_errors": True},
            review_policy={},
        )
        assert not evaluation.satisfied
        assert "agent-2" in evaluation.missing_members
        assert any("did not execute" in r for r in evaluation.reasons)

    def test_member_with_error_output(self):
        """A member whose output contains error indicators → not satisfied."""
        result = _result(
            member_results=[
                {"agent_id": "agent-1", "name": "Researcher", "output": {"content": "found data from sources [1]"}, "usage": None},
                {"agent_id": "agent-2", "name": "Writer", "output": {"error": "failed"}, "usage": None},
            ],
        )
        evaluation = evaluate_completion(
            result,
            assigned_agent_ids={"agent-1", "agent-2"},
            completion_config={"all_members_executed": True, "no_unresolved_errors": True},
            review_policy={},
        )
        assert not evaluation.satisfied
        assert "agent-2" in evaluation.error_members

    def test_member_with_empty_output(self):
        """A member with empty content → treated as error."""
        result = _result(
            member_results=[
                {"agent_id": "agent-1", "name": "Researcher", "output": {"content": ""}, "usage": None},
            ],
        )
        evaluation = evaluate_completion(
            result,
            assigned_agent_ids={"agent-1"},
            completion_config={"all_members_executed": True, "no_unresolved_errors": True},
            review_policy={},
        )
        assert not evaluation.satisfied
        assert "agent-1" in evaluation.error_members

    def test_member_with_failure_marker_in_string(self):
        """String output with failure markers → treated as error."""
        result = _result(
            member_results=[
                {"agent_id": "agent-1", "name": "Researcher", "output": "unable to complete the search", "usage": None},
            ],
        )
        evaluation = evaluate_completion(
            result,
            assigned_agent_ids={"agent-1"},
            completion_config={"all_members_executed": True, "no_unresolved_errors": True},
            review_policy={},
        )
        assert not evaluation.satisfied

    def test_no_completion_checks_disabled(self):
        """When all conditions are False, only basic checks apply."""
        result = _result(
            member_results=[],
        )
        evaluation = evaluate_completion(
            result,
            assigned_agent_ids={"agent-1"},
            completion_config={"all_members_executed": False, "no_unresolved_errors": False},
            review_policy={},
        )
        assert evaluation.satisfied

    def test_review_policy_empty_output_fails(self):
        """Active review policy with empty team output → not satisfied."""
        result = AgentExecutionResult(
            run_id="test-run",
            output={"content": ""},
            usage=None,
            member_results=[
                {"agent_id": "agent-1", "name": "Researcher", "output": {"content": "data with sources [1]"}, "usage": None},
            ],
        )
        evaluation = evaluate_completion(
            result,
            assigned_agent_ids={"agent-1"},
            completion_config={"all_members_executed": True, "no_unresolved_errors": True},
            review_policy={"max_rounds": 3},
        )
        assert not evaluation.satisfied
        assert any("empty" in r for r in evaluation.reasons)

    def test_route_mode_allows_extra_members(self):
        """Route mode may have members not in the plan; that's OK."""
        result = _result(
            member_results=[
                {"agent_id": "agent-1", "name": "Researcher", "output": {"content": "data with sources [1]"}, "usage": None},
                {"agent_id": "agent-extra", "name": "Extra", "output": {"content": "extra data with citations [2]"}, "usage": None},
            ],
        )
        evaluation = evaluate_completion(
            result,
            assigned_agent_ids={"agent-1"},
            completion_config={"all_members_executed": True, "no_unresolved_errors": True},
            review_policy={},
        )
        assert evaluation.satisfied


class TestInsufficientEvidence:
    """S3-补3: Detect members whose output lacks evidence of real work."""

    def test_short_uncited_output_flagged(self):
        """Short output without citations → insufficient evidence."""
        result = _result(
            member_results=[
                {"agent_id": "agent-1", "name": "Researcher", "output": {"content": "Done."}, "usage": None},
            ],
        )
        evaluation = evaluate_completion(
            result,
            assigned_agent_ids={"agent-1"},
            completion_config={"all_members_executed": True, "no_unresolved_errors": True},
            review_policy={"max_rounds": 2, "criteria": ["must cite sources"]},
        )
        assert not evaluation.satisfied
        assert "agent-1" in evaluation.insufficient_evidence_members

    def test_long_output_without_citations_passes(self):
        """Long output (>50 chars) is accepted even without explicit citations."""
        result = _result(
            member_results=[
                {"agent_id": "agent-1", "name": "Researcher", "output": {"content": "The analysis shows that market growth has been steady at 5% annually over the past three years based on the collected data."}, "usage": None},
            ],
        )
        evaluation = evaluate_completion(
            result,
            assigned_agent_ids={"agent-1"},
            completion_config={"all_members_executed": True, "no_unresolved_errors": True},
            review_policy={"max_rounds": 2, "criteria": ["must cite sources"]},
        )
        assert evaluation.satisfied

    def test_output_with_citation_passes_evidence_check(self):
        """Output with [1] style citations passes evidence check."""
        result = _result(
            member_results=[
                {"agent_id": "agent-1", "name": "Researcher", "output": {"content": "Found relevant data [1]."}, "usage": None},
            ],
        )
        evaluation = evaluate_completion(
            result,
            assigned_agent_ids={"agent-1"},
            completion_config={"all_members_executed": True, "no_unresolved_errors": True},
            review_policy={"max_rounds": 2, "criteria": ["must cite sources"]},
        )
        assert evaluation.satisfied

    def test_evidence_check_skipped_without_review_criteria(self):
        """Without review criteria, evidence check is not applied."""
        result = _result(
            member_results=[
                {"agent_id": "agent-1", "name": "Researcher", "output": {"content": "ok"}, "usage": None},
            ],
        )
        evaluation = evaluate_completion(
            result,
            assigned_agent_ids={"agent-1"},
            completion_config={"all_members_executed": True, "no_unresolved_errors": True},
            review_policy={"max_rounds": 2},  # no criteria
        )
        assert evaluation.satisfied

    def test_insufficient_evidence_markers_in_failure_detection(self):
        """S3-补3: 'insufficient evidence' and 'no sources' are failure markers."""
        result = _result(
            member_results=[
                {"agent_id": "agent-1", "name": "Researcher", "output": "insufficient evidence to complete", "usage": None},
            ],
        )
        evaluation = evaluate_completion(
            result,
            assigned_agent_ids={"agent-1"},
            completion_config={"all_members_executed": True, "no_unresolved_errors": True},
            review_policy={},
        )
        assert not evaluation.satisfied
        assert "agent-1" in evaluation.error_members

    def test_no_data_marker_treated_as_error(self):
        """'no data' in output → treated as error."""
        result = _result(
            member_results=[
                {"agent_id": "agent-1", "name": "Researcher", "output": "no data available for this query", "usage": None},
            ],
        )
        evaluation = evaluate_completion(
            result,
            assigned_agent_ids={"agent-1"},
            completion_config={"all_members_executed": True, "no_unresolved_errors": True},
            review_policy={},
        )
        assert not evaluation.satisfied


class TestBuildReviewFeedback:
    """Review feedback text generation for re-execution."""

    def test_feedback_includes_reasons(self):
        evaluation = CompletionEvaluation(
            satisfied=False,
            reasons=["Members did not execute: agent-2", "Members with errors: agent-3"],
            missing_members=["agent-2"],
            error_members=["agent-3"],
        )
        feedback = build_review_feedback(evaluation, 1)
        assert "Review round 1" in feedback
        assert "did not execute" in feedback
        assert "Members with errors" in feedback
        assert "agent-2" in feedback
        assert "agent-3" in feedback

    def test_feedback_round_number(self):
        evaluation = CompletionEvaluation(
            satisfied=False,
            reasons=["test reason"],
            missing_members=[],
            error_members=[],
        )
        feedback = build_review_feedback(evaluation, 3)
        assert "Review round 3" in feedback


    def test_substantive_report_mentioning_no_data_passes(self):
        """A detailed multi-paragraph report (>240 chars) noting 'no data' for one sub-item is not a failure."""
        long_report = (
            "Comprehensive corporate due diligence completed across 5 authoritative registries [1] [2]. "
            "Business registration, paid-in capital, and executive credentials are fully verified. "
            "Regarding historical environmental penalties, there is no data indicating any administrative "
            "violations between 2021 and 2026 [3]. Overall compliance posture is satisfactory."
        )
        result = _result(
            member_results=[
                {"agent_id": "agent-1", "name": "Auditor", "output": {"content": long_report}, "usage": None},
            ],
        )
        evaluation = evaluate_completion(
            result,
            assigned_agent_ids={"agent-1"},
            completion_config={"all_members_executed": True, "no_unresolved_errors": True},
            review_policy={},
        )
        assert evaluation.satisfied
