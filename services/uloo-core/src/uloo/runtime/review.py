# AI生成
"""Post-execution review and completion-condition evaluation (S3-补1, S3-补3).

Evaluates whether a team execution truly satisfies the declared completion
conditions and review policy.  This is a *programmatic* check – the LLM
coordinator receives the same policies as instructions during execution
(see factory.build_team), but we independently verify the structural
outcomes so that a model that merely *claims* success cannot mark a run
as complete when members are missing, results are empty, or evidence is
insufficient.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from .execution import AgentExecutionResult


@dataclass(frozen=True)
class CompletionEvaluation:
    """Result of evaluating completion conditions against an execution."""

    satisfied: bool
    reasons: list[str] = field(default_factory=list)
    missing_members: list[str] = field(default_factory=list)
    error_members: list[str] = field(default_factory=list)
    insufficient_evidence_members: list[str] = field(default_factory=list)


# S3-补3: Expanded failure markers — the model may emit these phrases instead
# of raising an exception, claiming success without actually completing work.
_FAILURE_MARKERS = (
    "unable to",
    "cannot complete",
    "tool failed",
    "no results",
    "error:",
    "insufficient evidence",
    "not enough information",
    "no data",
    "cannot verify",
    "no sources",
    "no references",
    "could not find",
    "did not find",
    "no relevant",
)

# Evidence indicators — presence of these in output suggests the member
# actually performed work and cited sources.
_EVIDENCE_PATTERNS = [
    re.compile(r"\b(?:source|citation|reference|ref|link|url|http)\b", re.IGNORECASE),
    re.compile(r"\[\d+\]"),  # [1], [2] style citations
    re.compile(r"```"),  # code blocks indicate concrete output
]


def _detect_member_error(output: Any) -> bool:
    """Heuristic check for error indicators in a member's output.

    S3-补3: A member that returns text containing failure markers is treated
    as having an unresolved error, even if the model claims success.
    """
    if output is None:
        return True
    if isinstance(output, dict):
        # Check for explicit error fields
        for key in ("error", "failed", "status"):
            val = output.get(key)
            if isinstance(val, str) and val.lower() in ("error", "failed", "failed_error"):
                return True
        # Empty content
        content = output.get("content", output.get("items"))
        if content is None or (isinstance(content, str) and not content.strip()):
            return True
        # Check failure markers in content string
        if isinstance(content, str):
            stripped = content.strip().lower()
            if any(marker in stripped for marker in _FAILURE_MARKERS):
                return True
        return False
    if isinstance(output, str):
        stripped = output.strip().lower()
        if not stripped:
            return True
        return any(marker in stripped for marker in _FAILURE_MARKERS)
    return False


def _detect_insufficient_evidence(output: Any) -> bool:
    """Check if a member's output lacks evidence of actual work.

    S3-补3: A member that returns a short text without any citations, sources,
    or concrete artifacts may be claiming success without doing real work.
    This is a softer check than _detect_member_error — it only flags
    outputs that are both short AND lack evidence markers.
    """
    if output is None:
        return True

    # Extract text content for analysis
    if isinstance(output, dict):
        content = output.get("content", output.get("items"))
        if isinstance(content, str):
            text = content
        elif isinstance(content, list) and content:
            return False  # Non-empty list items count as evidence
        else:
            return True
    elif isinstance(output, str):
        text = output
    else:
        return False

    stripped = text.strip()
    if not stripped:
        return True

    # Very short outputs (< 50 chars) without evidence markers are suspicious
    if len(stripped) < 50:
        has_evidence = any(pattern.search(stripped) for pattern in _EVIDENCE_PATTERNS)
        if not has_evidence:
            return True

    return False


def evaluate_completion(
    result: AgentExecutionResult,
    *,
    assigned_agent_ids: set[str],
    completion_config: dict[str, Any],
    review_policy: dict[str, Any],
) -> CompletionEvaluation:
    """Evaluate whether the execution satisfies the declared completion conditions.

    S3-补3: Success is not determined solely by the model returning a final
    text. We independently verify:
      1. All assigned members actually executed (not just claimed to have).
      2. No member output contains error/failure markers or is empty.
      3. No member output shows signs of insufficient evidence (short, uncited).
      4. The team synthesis is non-empty when a review policy is active.

    Args:
        result: The AgentExecutionResult from the team run.
        assigned_agent_ids: IDs of agents that were assigned work in the plan.
        completion_config: The team's completion config dict.
        review_policy: The team's review_policy dict.

    Returns:
        CompletionEvaluation with satisfied=True only if all enabled conditions pass.
    """
    reasons: list[str] = []
    missing_members: list[str] = []
    error_members: list[str] = []
    insufficient_evidence_members: list[str] = []

    executed_ids = {member["agent_id"] for member in result.member_results}

    # Condition 1: all_members_executed — key members must have actually run
    if completion_config.get("all_members_executed", True):
        not_executed = sorted(assigned_agent_ids - executed_ids)
        if not_executed:
            missing_members = not_executed
            reasons.append(
                f"Assigned members did not execute: {', '.join(not_executed)}"
            )

    # Condition 2: no_unresolved_errors — tool failures and error markers
    if completion_config.get("no_unresolved_errors", True):
        for member in result.member_results:
            agent_id = member["agent_id"]
            if _detect_member_error(member.get("output")):
                error_members.append(agent_id)
        if error_members:
            reasons.append(
                f"Members with unresolved errors, tool failures, or empty results: "
                f"{', '.join(sorted(error_members))}"
            )

    # Condition 3 (S3-补3): no_insufficient_evidence — outputs must show real work
    # Check this when review policy has criteria or when completion requires it
    check_evidence = (
        completion_config.get("no_unresolved_errors", True)
        and bool(review_policy.get("criteria"))
    )
    if check_evidence:
        for member in result.member_results:
            agent_id = member["agent_id"]
            if agent_id not in error_members and _detect_insufficient_evidence(member.get("output")):
                insufficient_evidence_members.append(agent_id)
        if insufficient_evidence_members:
            reasons.append(
                f"Members with insufficient evidence (short or uncited output): "
                f"{', '.join(sorted(insufficient_evidence_members))}"
            )

    # Condition 4: review_policy max_rounds > 0 means at least one review is expected.
    # We can't programmatically verify review quality, but we can check that
    # the coordinator produced a non-empty synthesis.
    if review_policy.get("max_rounds", 0) > 0:
        output = result.output
        if isinstance(output, dict):
            content = output.get("content", output.get("items"))
            if content is None or (isinstance(content, str) and not content.strip()):
                reasons.append("Team output is empty despite active review policy")
        elif isinstance(output, str) and not output.strip():
            reasons.append("Team output is empty despite active review policy")

    satisfied = len(reasons) == 0
    return CompletionEvaluation(
        satisfied=satisfied,
        reasons=reasons,
        missing_members=missing_members,
        error_members=error_members,
        insufficient_evidence_members=insufficient_evidence_members,
    )


def build_review_feedback(evaluation: CompletionEvaluation, round_num: int) -> str:
    """Build feedback text for a re-execution round after failed review.

    Args:
        evaluation: The failed CompletionEvaluation from the previous round.
        round_num: The review round number (1-based).

    Returns:
        Feedback string to prepend to the next execution prompt.
    """
    parts = [f"Review round {round_num}: previous execution did not meet completion conditions."]
    for reason in evaluation.reasons:
        parts.append(f"  - {reason}")
    if evaluation.missing_members:
        parts.append(
            f"  Members needing rework: {', '.join(evaluation.missing_members)}. "
            "Re-execute these members and collect their output."
        )
    if evaluation.error_members:
        parts.append(
            f"  Members with errors: {', '.join(evaluation.error_members)}. "
            "Address the errors or tool failures before claiming completion."
        )
    parts.append("Do not repeat previous failed approaches. Address the specific issues above.")
    return "\n".join(parts)
