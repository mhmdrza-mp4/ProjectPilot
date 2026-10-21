"""Decision review & validation: is this DecisionResult presentable?

Pipeline position (last, read-only):

    DecisionResult (+ optional ScheduleAnalysis, consequence evaluations)
            ↓  deterministic validation + aggregation
    DecisionReview (status one of READY / REVIEW_REQUIRED / INVALID)

Deliberate boundaries (a status layer, not a decision layer):
- this module NEVER recomputes scores, re-runs conflict analysis, calls
  JEV or MSP, invents values, or mutates anything. It reads the numbers
  the pipeline already produced and reports their review surface;
- ordinary missing OPTIONAL business data (slack, CD, successors) is
  REVIEW_REQUIRED, never INVALID: only structural/invariant problems —
  duplicate or unknown task ids, missing/extra candidates, broken rank
  sequences, a mismatched planning period — make a result INVALID;
- no second reason vocabulary competes with the pipeline's user-facing
  reason strings: those are preserved verbatim on the ranked tasks. The
  review layer adds a SMALL fixed set of aggregated, machine-readable
  categories (see the constants below) used ONLY for counting here;
- low JEV confidence is detected from the existing evaluation review
  reasons (the stable prefix ``"JEV confidence is low"``) — confidence
  is a review signal, never an invalid one and never recomputed.

Status precedence: INVALID > REVIEW_REQUIRED > READY.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from project_decision.application.consequence_evaluation import (
    STATUS_FAILED,
    STATUS_UNAVAILABLE,
    ConsequenceEvaluation,
)
from project_decision.application.priority_ranking import DecisionResult, RankedTask
from project_decision.application.schedule_analysis import ScheduleAnalysis
from project_decision.domain.planning_session import PlanningSession
from project_decision.domain.project_state import ProjectState

# Stable prefix of the low-confidence review reason produced by
# consequence_evaluation ("JEV confidence is low (x < t); ...").
_LOW_CONFIDENCE_REASON_PREFIX = "JEV confidence is low"


class ReviewStatus(Enum):
    """Overall review outcome (precedence: INVALID > REVIEW_REQUIRED > READY)."""

    READY = "ready"
    REVIEW_REQUIRED = "review_required"
    INVALID = "invalid"


# Machine-readable review categories (fixed order → deterministic counts).
# NOTE: raw user-facing reason strings on TaskPriorityScore /
# ConsequenceEvaluation are NEVER replaced by these.
UNSCORED_CANDIDATE = "unscored_candidate"
MISSING_SCHEDULE_DATA = "missing_schedule_data"
PROVISIONAL_CONSEQUENCE = "provisional_consequence"
LOW_CONSEQUENCE_CONFIDENCE = "low_consequence_confidence"
CONSEQUENCE_EVALUATION_FAILED = "consequence_evaluation_failed"
CONSEQUENCE_EVALUATION_UNAVAILABLE = "consequence_evaluation_unavailable"
RESOURCE_CONFLICT = "resource_conflict"

_CATEGORY_ORDER = (
    UNSCORED_CANDIDATE,
    MISSING_SCHEDULE_DATA,
    PROVISIONAL_CONSEQUENCE,
    LOW_CONSEQUENCE_CONFIDENCE,
    CONSEQUENCE_EVALUATION_FAILED,
    CONSEQUENCE_EVALUATION_UNAVAILABLE,
    RESOURCE_CONFLICT,
)


@dataclass(frozen=True)
class DecisionReview:
    """Aggregated review surface of one DecisionResult (reference-level).

    Task ids and counts only — complete scores/tasks are NOT copied;
    every per-task explanation stays on the existing RankedTask /
    TaskPriorityScore objects. ``review_reason_counts`` maps the fixed
    category constants to the number of ranked tasks carrying them
    (a task may contribute to several categories); deterministic order,
    zero entries omitted.
    """

    status: ReviewStatus
    validation_issues: tuple[str, ...]
    candidate_count: int
    ranked_count: int
    tasks_needing_review: tuple[str, ...]
    review_reason_counts: Mapping[str, int]
    resource_warning_count: int
    tasks_with_resource_warnings: tuple[str, ...]
    missing_schedule_count: int
    tasks_with_missing_schedule: tuple[str, ...]
    provisional_consequence_count: int
    low_confidence_consequence_count: int
    unscored_task_ids: tuple[str, ...]

    @property
    def unscored_count(self) -> int:
        return len(self.unscored_task_ids)


def validate_decision_result(
    session: PlanningSession,
    state: ProjectState,
    decision_result: DecisionResult,
) -> tuple[str, ...]:
    """Structural invariants of one DecisionResult (deterministic issues).

    Checks: period match; ranked ids ⊆ session candidates; ids resolve
    in the state with matching names; no duplicate/missing candidates;
    ranks exactly 1..n; ranked size equals the candidate count. Inputs
    are never mutated. Empty tuple = structurally valid.
    """
    ranked = decision_result.ranked_tasks
    issues: list[str] = []

    if decision_result.planning_period != session.planning_period:
        issues.append(
            "planning period mismatch: the decision result was built for a "
            f"different period (result: {decision_result.planning_period}, "
            f"session: {session.planning_period})"
        )

    ranked_ids = [ranked.task_id for ranked in ranked]

    counted: dict[str, int] = {}
    for task_id in ranked_ids:
        counted[task_id] = counted.get(task_id, 0) + 1
    for task_id, occurrences in counted.items():  # first-occurrence order
        if occurrences > 1:
            issues.append(
                f"ranked task '{task_id}' appears {occurrences} times; a "
                "candidate may appear exactly once"
            )

    state_tasks = {task.id: task for task in state.tasks}
    candidate_ids = set(session.candidate_task_ids)
    for ranked_item in ranked:
        task = state_tasks.get(ranked_item.task_id)
        if ranked_item.task_id not in candidate_ids:
            issues.append(
                f"ranked task '{ranked_item.task_id}' is not a session candidate"
            )
        if task is None:
            issues.append(
                f"ranked task '{ranked_item.task_id}' does not exist in the "
                "project state"
            )
        elif ranked_item.task_name != task.name:
            issues.append(
                f"ranked task '{ranked_item.task_id}' has name "
                f"{ranked_item.task_name!r}; the project state says "
                f"{task.name!r}"
            )

    for candidate_id in session.candidate_task_ids:
        if candidate_id not in counted:
            issues.append(
                f"candidate '{candidate_id}' is missing from the ranked result"
            )

    for index, ranked_item in enumerate(ranked):
        expected = index + 1
        if ranked_item.rank != expected:
            issues.append(
                f"ranked task '{ranked_item.task_id}' has rank "
                f"{ranked_item.rank}; expected {expected}"
            )

    if len(ranked) != len(session.candidate_task_ids):
        issues.append(
            f"candidate count mismatch: the result lists {len(ranked)} ranked "
            f"tasks for a session with {len(session.candidate_task_ids)} "
            "candidates"
        )

    return tuple(issues)


def review_decision_result(
    session: PlanningSession,
    state: ProjectState,
    decision_result: DecisionResult,
    schedule_analysis: ScheduleAnalysis | None = None,
    consequence_evaluations: Sequence[ConsequenceEvaluation]
    | Mapping[str, ConsequenceEvaluation]
    | None = None,
) -> DecisionReview:
    """Review one DecisionResult: status, issues, and deterministic counts.

    ``schedule_analysis`` and ``consequence_evaluations`` are optional,
    matching the pipeline's conventions: when absent, score metadata
    already carried by the ranked tasks is used instead of recomputing.
    Evaluations for tasks outside the ranked result are ignored.
    Deterministic, side-effect free; MSP/JEV are never contacted.
    """
    issues = validate_decision_result(session, state, decision_result)
    evaluations_by_id = _normalize_evaluations(consequence_evaluations)

    categories = tuple(
        _categories(ranked, schedule_analysis, evaluations_by_id)
        for ranked in decision_result.ranked_tasks
    )

    counts: dict[str, int] = {}
    for category in _CATEGORY_ORDER:
        amount = sum(1 for task_categories in categories if category in task_categories)
        if amount:
            counts[category] = amount

    tasks_needing_review = tuple(
        ranked.task_id
        for ranked, ranked_categories in zip(decision_result.ranked_tasks, categories)
        if ranked_categories or ranked.needs_review
    )
    tasks_with_resource_warnings = tuple(
        ranked.task_id for ranked in decision_result.ranked_tasks if ranked.resource_warning
    )
    tasks_with_missing_schedule = tuple(
        ranked.task_id
        for ranked, ranked_categories in zip(decision_result.ranked_tasks, categories)
        if MISSING_SCHEDULE_DATA in ranked_categories
    )
    unscored = tuple(
        ranked.task_id
        for ranked in decision_result.ranked_tasks
        if ranked.score is None
    )

    if issues:
        status = ReviewStatus.INVALID
    elif tasks_needing_review:
        status = ReviewStatus.REVIEW_REQUIRED
    else:
        status = ReviewStatus.READY

    return DecisionReview(
        status=status,
        validation_issues=issues,
        candidate_count=len(session.candidate_task_ids),
        ranked_count=len(decision_result.ranked_tasks),
        tasks_needing_review=tasks_needing_review,
        review_reason_counts=counts,
        resource_warning_count=len(tasks_with_resource_warnings),
        tasks_with_resource_warnings=tasks_with_resource_warnings,
        missing_schedule_count=len(tasks_with_missing_schedule),
        tasks_with_missing_schedule=tasks_with_missing_schedule,
        provisional_consequence_count=counts.get(PROVISIONAL_CONSEQUENCE, 0),
        low_confidence_consequence_count=counts.get(LOW_CONSEQUENCE_CONFIDENCE, 0),
        unscored_task_ids=unscored,
    )


def _normalize_evaluations(
    consequence_evaluations: Sequence[ConsequenceEvaluation]
    | Mapping[str, ConsequenceEvaluation]
    | None,
) -> dict[str, ConsequenceEvaluation]:
    if consequence_evaluations is None:
        return {}
    if isinstance(consequence_evaluations, Mapping):
        return dict(consequence_evaluations)
    return {evaluation.task_id: evaluation for evaluation in consequence_evaluations}


def _categories(
    ranked: RankedTask,
    schedule_analysis: ScheduleAnalysis | None,
    evaluations_by_id: Mapping[str, ConsequenceEvaluation],
) -> tuple[str, ...]:
    """Fixed-order review categories for one ranked task (no rewriting)."""
    categories: list[str] = []
    score = ranked.score

    if score is None:
        # Unscored candidates carry no score metadata; they are reported
        # once as unscored and never double-flagged with fake criteria.
        categories.append(UNSCORED_CANDIDATE)
    else:
        score_schedule_missing = (
            schedule_analysis is not None
            and ranked.task_id not in schedule_analysis.schedules
        )
        if (
            score_schedule_missing
            or score.schedule_pressure is None
            or score.successor_impact is None
        ):
            categories.append(MISSING_SCHEDULE_DATA)
        if score.consequence_source == "provisional":
            categories.append(PROVISIONAL_CONSEQUENCE)
    if ranked.resource_warning:
        categories.append(RESOURCE_CONFLICT)

    evaluation = evaluations_by_id.get(ranked.task_id)
    if evaluation is not None:
        if evaluation.status == STATUS_FAILED:
            categories.append(CONSEQUENCE_EVALUATION_FAILED)
        elif evaluation.status == STATUS_UNAVAILABLE:
            categories.append(CONSEQUENCE_EVALUATION_UNAVAILABLE)
        if any(
            reason.startswith(_LOW_CONFIDENCE_REASON_PREFIX)
            for reason in evaluation.review_reasons
        ):
            categories.append(LOW_CONSEQUENCE_CONFIDENCE)
    return tuple(categories)
