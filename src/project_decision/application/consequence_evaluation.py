"""Consequence of Delay via Jev — the bridge between Jev and priority scoring.

Pipeline position:

    Task name + notes
            ↓   (ConsequenceOfDelay question, levels 0..3)
        Jev (official TypeSafe SDK, via jev_service)
            ↓   ConsequenceEvaluation (score, confidence, probabilities)
        CD = jev_score / 3
            ↓
    existing score_task() → TaskPriorityScore

Deliberate boundaries:
- The Jev evaluation asks ONLY about the worst NON-SCHEDULE business
  consequence of a late finish. The question and the input state never
  contain slack, criticality, successors, dates, resources, period, or
  scores — those live in Schedule Pressure / Successor Impact, and
  leaking them here would double-count schedule information.
- Every Jev answer is routed through ``jev_service.send_jev_questions``
  (the existing boundary); this module never touches the TypeSafe SDK.
- Missing notes → Jev is NOT called; the evaluation is honestly
  ``unavailable`` and scoring receives no CD (its existing provisional
  0.5 + score-range behavior applies unchanged).
- Jev failure, timeout, malformed response, invalid score → a
  structured ``failed`` evaluation; the planning session never crashes,
  every failing task is isolated, and there is NO automatic retry.
- Confidence NEVER modifies the priority score. It is a review signal:
  below the threshold the evaluation (and the merged scoring result)
  is marked for manager review while the normal Jev-derived CD is kept.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from project_decision.application.jev_service import JevCallResult, send_jev_questions
from project_decision.application.priority_scoring import (
    ScoringParameters,
    TaskPriorityScore,
    score_planning_session,
)
from project_decision.application.schedule_analysis import ScheduleAnalysis
from project_decision.config import TypesafeSettings
from project_decision.domain.planning_session import PlanningSession
from project_decision.domain.project_state import ProjectState
from project_decision.infrastructure.jev.models import JevScoreAnswer, JevScoreQuestion

# Question identity and semantics (four SITUATIONAL levels; the names in
# the level texts describe concrete situations, not vague degrees).
CONSEQUENCE_QUESTION_NAME = "consequence_of_delay"
CONSEQUENCE_INSTRUCTIONS = (
    "What is the worst NON-SCHEDULE business consequence (cost, safety, "
    "quality, contract, production) if this activity finishes late? "
    "Judge only the business impact of the delay itself."
)
CONSEQUENCE_LEVELS: tuple[str, ...] = (
    "Level 0 — Only the activity itself is delayed; there is no material "
    "downstream non-schedule business consequence.",
    "Level 1 — The delay causes a small, recoverable consequence, such as "
    "limited added cost or rework.",
    "Level 2 — The delay causes a significant consequence involving cost, "
    "quality risk, or a customer/supplier commitment.",
    "Level 3 — The delay can cause a major consequence: production "
    "stoppage, a safety/regulatory violation, a contractual penalty, or "
    "the loss of a non-movable production/window/resource opportunity.",
)
CONSEQUENCE_MAX_LEVEL = 3

# Review thresholds/marking (confidence is only a review signal, never a
# multiplier of CD or the score).
LOW_CONFIDENCE_THRESHOLD = 0.5

STATUS_EVALUATED = "evaluated"
STATUS_UNAVAILABLE = "unavailable"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class ConsequenceEvaluation:
    """One candidate's Jev consequence-of-delay outcome, fully explainable.

    ``status``: ``evaluated`` (CD computed from the Jev score),
    ``unavailable`` (NOT sent to Jev — missing/insufficient notes, or
    the task is missing from the state), or ``failed`` (the Jev call or
    its answer was unusable). ``consequence_of_delay`` is None unless
    evaluated — scoring then applies its own provisional behavior.
    The original Jev information (score, confidence, per-level
    probabilities, legend) is preserved untouched for future UI, and
    ``confidence`` never modifies CD.
    """

    task_id: str
    status: str
    consequence_of_delay: float | None = None
    jev_score: float | None = None
    confidence: float | None = None
    probabilities: dict[int, float] | None = None
    legend: dict[int, Any] | None = None
    review_reasons: tuple[str, ...] = ()
    error_message: str | None = None
    error_kind: str | None = None


def consequence_question() -> JevScoreQuestion:
    """The fixed consequence-of-delay Score question (levels 0..3)."""
    return JevScoreQuestion(
        instructions=CONSEQUENCE_INSTRUCTIONS,
        criteria=list(CONSEQUENCE_LEVELS),
    )


def consequence_state(name: str, notes: str) -> dict[str, Any]:
    """The reasoning state Jev sees: ONLY task name and notes.

    Never: slack, criticality, successors, dates, resources, period,
    scores. Those are Schedule Pressure / Successor Impact inputs and
    would double-count if Jev also saw them.
    """
    return {"task": {"name": name, "notes": notes}}


def consequence_of_delay_from_score(score: float, *, max_level: int = CONSEQUENCE_MAX_LEVEL) -> float:
    """CD = jev_score / max_level; the score is validated, never clamped.

    The Jev score may be fractional (the official Score answer is a
    probability-weighted level average), but it must stay within
    [0, max_level]; anything else is invalid input, not a lower value.
    """
    if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0.0 <= float(score) <= float(max_level):
        raise ValueError(f"invalid Jev consequence score: {score!r} (expected 0..{max_level})")
    return float(score) / float(max_level)


def missing_notes_reason() -> str:
    return "JEV evaluation unavailable: task notes are missing or insufficient."


def evaluate_session_consequences(
    session: PlanningSession,
    state: ProjectState,
    *,
    send: Callable[..., JevCallResult] = send_jev_questions,
    settings: TypesafeSettings | None = None,
    env_file_directory: Path | None = None,
    client_factory: Any | None = None,
    confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD,
) -> tuple[ConsequenceEvaluation, ...]:
    """Evaluate every candidate's Consequence of Delay, in candidate order.

    Deterministic, read-only (state/session never mutated), one Jev
    request per candidate. An isolated failure (API error, malformed
    answer, invalid score) NEVER aborts the remaining candidates and is
    never retried automatically.
    """
    tasks_by_id = {task.id: task for task in state.tasks}
    evaluations: list[ConsequenceEvaluation] = []
    for candidate_id in session.candidate_task_ids:
        task = tasks_by_id.get(candidate_id)
        if task is None:
            evaluations.append(ConsequenceEvaluation(
                task_id=candidate_id,
                status=STATUS_UNAVAILABLE,
                review_reasons=(
                    f"JEV evaluation unavailable: task '{candidate_id}' is not "
                    "in the project state.",
                ),
            ))
        elif not task.notes:
            evaluations.append(ConsequenceEvaluation(
                task_id=candidate_id,
                status=STATUS_UNAVAILABLE,
                review_reasons=(missing_notes_reason(),),
            ))
        else:
            evaluations.append(
                _evaluate_one(
                    task_id=candidate_id,
                    name=task.name,
                    notes=task.notes,
                    send=send,
                    settings=settings,
                    env_file_directory=env_file_directory,
                    client_factory=client_factory,
                    confidence_threshold=confidence_threshold,
                )
            )
    return tuple(evaluations)


def _evaluate_one(
    task_id: str,
    name: str,
    notes: str,
    *,
    send: Callable[..., JevCallResult],
    settings: TypesafeSettings | None,
    env_file_directory: Path | None,
    client_factory: Any | None,
    confidence_threshold: float,
) -> ConsequenceEvaluation:
    """One Jev request for one task, mapped into a safe result."""
    call = send(
        consequence_state(name, notes),
        {CONSEQUENCE_QUESTION_NAME: consequence_question()},
        settings=settings,
        env_file_directory=env_file_directory,
        client_factory=client_factory,
    )
    if not call.ok:
        # send_jev_questions already redacts the key from messages.
        return ConsequenceEvaluation(
            task_id=task_id,
            status=STATUS_FAILED,
            error_message=call.error_message,
            error_kind=call.error_kind,
            review_reasons=(f"JEV evaluation failed: {call.error_message}",),
        )
    answer = call.answers.get(CONSEQUENCE_QUESTION_NAME)
    if not isinstance(answer, JevScoreAnswer):
        detail = "no answer returned" if answer is None else "unexpected answer type"
        return ConsequenceEvaluation(
            task_id=task_id,
            status=STATUS_FAILED,
            error_kind="unexpected_response",
            review_reasons=(f"JEV evaluation failed: {detail} for the consequence question.",),
        )
    try:
        cd = consequence_of_delay_from_score(answer.score)
    except ValueError as exc:
        return ConsequenceEvaluation(
            task_id=task_id,
            status=STATUS_FAILED,
            jev_score=answer.score,
            confidence=answer.confidence,
            probabilities=dict(answer.probabilities) if answer.probabilities else None,
            legend=dict(answer.legend) if answer.legend else None,
            error_message=str(exc),
            error_kind="unexpected_response",
            review_reasons=(f"JEV evaluation failed: invalid consequence score.",),
        )
    review_reasons: list[str] = []
    if answer.confidence is not None and answer.confidence < confidence_threshold:
        review_reasons.append(
            f"JEV confidence is low ({answer.confidence} < {confidence_threshold}); "
            "manager review recommended. The Jev value is still used in the score."
        )
    return ConsequenceEvaluation(
        task_id=task_id,
        status=STATUS_EVALUATED,
        consequence_of_delay=cd,
        jev_score=answer.score,
        confidence=answer.confidence,
        probabilities=dict(answer.probabilities) if answer.probabilities else None,
        legend=dict(answer.legend) if answer.legend else None,
        review_reasons=tuple(review_reasons),
    )


def score_session_with_evaluations(
    session: PlanningSession,
    schedule_analysis: ScheduleAnalysis,
    evaluations: Mapping[str, ConsequenceEvaluation]
    | tuple[ConsequenceEvaluation, ...],
    *,
    parameters: ScoringParameters | None = None,
) -> tuple[TaskPriorityScore, ...]:
    """Thread Jev consequence evaluations into the EXISTING scoring pipeline.

    Only evaluated consequences enter as CD; everything else keeps the
    scoring module's own provisional behavior (neutral 0.5 + score
    range). This layer adds NOTHING to the formulas: Schedule Pressure,
    Successor Impact, the weights, confidence handling and criticality
    are untouched — evaluation review reasons are merged into each
    task's review information so one result explains the whole story.
    """
    if not isinstance(evaluations, Mapping):
        evaluations = {evaluation.task_id: evaluation for evaluation in evaluations}
    consequence_by_task = {
        task_id: evaluation.consequence_of_delay
        for task_id, evaluation in evaluations.items()
        if evaluation.status == STATUS_EVALUATED
        and evaluation.consequence_of_delay is not None
    }
    scores = score_planning_session(
        session,
        schedule_analysis,
        consequence_of_delay=consequence_by_task,
        parameters=parameters,
    )
    return tuple(
        _merge_review_information(score, evaluations.get(score.task_id))
        for score in scores
    )


def _merge_review_information(
    score: TaskPriorityScore,
    evaluation: ConsequenceEvaluation | None,
) -> TaskPriorityScore:
    """Merge consequence-evaluation review causes into the score result.

    The cause ("notes missing", "JEV failed: ...", "low confidence")
    comes from the evaluation; the score stays numerically identical —
    this only enriches the explanation/report marks.
    """
    if evaluation is None or not evaluation.review_reasons:
        return score
    return replace(
        score,
        needs_review=True,
        review_reasons=score.review_reasons + evaluation.review_reasons,
    )
