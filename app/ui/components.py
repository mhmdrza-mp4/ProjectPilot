"""UI building blocks: pure formatting/transformation helpers + factories.

This module contains NO rendering and NO business rules: it formats
values the application layer already produced, transforms results into
display rows, and builds the injected loader/evaluator callables for
the workflow. Every number shown in the UI originates from the
application pipeline — formatting never changes values. Derived row
groupings (what to show where) are presentation only; conditions,
statuses, and counts always come from the application models.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date

from project_decision.application.consequence_evaluation import (
    STATUS_FAILED,
    STATUS_UNAVAILABLE,
)
from project_decision.application.decision_pipeline import DecisionPipelineResult
from project_decision.application.decision_review import DecisionReview, ReviewStatus
from project_decision.application.decision_workflow import DecisionWorkflowState
from project_decision.application.priority_scoring import ScoringParameters
from project_decision.application.priority_ranking import RankedTask
from project_decision.application.resource_conflicts import ConflictStatus
from project_decision.domain.project_state import ProjectState

_NOT_AVAILABLE = "—"

# Weight keys of the scoring-parameter editor (same field names as the
# existing ScoringParameters; validation is the model's, not the UI's).
PARAMETER_FIELDS = (
    "schedule_pressure_weight",
    "successor_impact_weight",
    "consequence_of_delay_weight",
)

# (badge label, CSS class) — existing review status values only.
_STATUS_BADGES: dict[str, tuple[str, str]] = {
    ReviewStatus.READY.value: ("READY", "ready"),
    ReviewStatus.REVIEW_REQUIRED.value: ("REVIEW REQUIRED", "review"),
    ReviewStatus.INVALID.value: ("INVALID", "invalid"),
}

# Short status explanations (presentation text for the existing status).
_STATUS_EXPLANATIONS: dict[str, str] = {
    ReviewStatus.READY.value: (
        "Every ranked activity carries a complete explanation; "
        "nothing needs manager review before use."
    ),
    ReviewStatus.REVIEW_REQUIRED.value: (
        "Some activities need review before conclusions are drawn — "
        "see the Review Center below for the exact reasons."
    ),
    ReviewStatus.INVALID.value: (
        "The result is structurally invalid and must not be used for a "
        "decision. Check the validation issues below."
    ),
}


# -- formatting helpers (values are never altered) ------------------------------


def optional_text(value: object) -> str:
    """None-safe display text (never a fabricated placeholder number)."""
    if value is None:
        return _NOT_AVAILABLE
    return str(value)


def score_text(value: float | None) -> str:
    """Priority score 0..100 with one decimal."""
    if value is None:
        return _NOT_AVAILABLE
    return f"{value:.1f}"


def criterion_text(value: float | None) -> str:
    """Normalized criterion (0..1) shown as a percentage."""
    if value is None:
        return _NOT_AVAILABLE
    return f"{value * 100:.0f}%"


def slack_text(value: float | None) -> str:
    """Slack in working days; negative (already-late) stays negative."""
    if value is None:
        return _NOT_AVAILABLE
    return f"{value:.1f} d"


def date_text(value: date | None) -> str:
    if value is None:
        return _NOT_AVAILABLE
    return value.isoformat()


def review_text(ranked: RankedTask) -> str:
    """A compact review marker plus the number of explanation reasons."""
    if ranked.score is None:
        return "unscored"
    if not ranked.needs_review:
        return "ok"
    return f"review ({len(ranked.review_reasons)} reason(s))"


def row_state(ranked: RankedTask) -> str:
    """Presentation state of one ranked row: what the row should look like.

    Pure composition of EXISTING flags for visual distinction only
    (priority: unscored > resource warning > review > ok). It never
    changes rank, score, or review semantics.
    """
    if ranked.score is None:
        return "UNSCORED"
    if ranked.resource_warning:
        return "RESOURCE WARNING"
    if ranked.needs_review:
        return "REVIEW"
    return "OK"


# -- status / guards ------------------------------------------------------------


def status_badge(status: ReviewStatus) -> tuple[str, str]:
    """(label, CSS class) for one review-status value."""
    label, css_class = _STATUS_BADGES[status.value]
    return label, css_class


def status_explanation(status: ReviewStatus) -> str:
    """A short 'what does this status mean' text (existing semantics)."""
    return _STATUS_EXPLANATIONS[status.value]


def can_run_decision(
    workflow: DecisionWorkflowState | None,
) -> tuple[bool, str | None]:
    """The run conditions, stated by the workflow's own state (no rules)."""
    if workflow is None:
        return False, "Load a project first."
    if workflow.planning_session is None:
        return False, "Select a planning period first."
    if not workflow.confirmed:
        return False, "Confirm the planning session first."
    return True, None


def clear_if_stale(
    result: DecisionPipelineResult | None,
    workflow: DecisionWorkflowState | None,
) -> tuple[DecisionPipelineResult | None, str | None]:
    """Drop a stored result when its session is no longer the current one.

    Stays the SINGLE session-staleness mechanism (the workflow's own
    ``result_is_stale``); the UI only decides how to present the loss.
    """
    from project_decision.application.decision_workflow import result_is_stale

    if workflow is None or not result_is_stale(workflow, result):
        return result, None
    return (
        None,
        "The planning session changed — the previous result was cleared. "
        "Run the decision analysis again for the current session.",
    )


def clear_if_parameters_changed(
    stored_parameters: ScoringParameters | None,
    current_parameters: ScoringParameters,
) -> tuple[bool, str | None]:
    """Whether the scoring parameters changed since the run (UI config only).

    The pipeline result does not (and should not) store its parameters,
    so the UI remembers them next to the result. A mismatch requires a
    rerun; the application-level session staleness check is untouched.
    """
    if stored_parameters is None or stored_parameters == current_parameters:
        return False, None
    return True, (
        "The scoring parameters changed since this run - the previous "
        "result was cleared. Run the decision analysis again for the "
        "current parameters."
    )


INVALID_PARAMETERS_MESSAGE = (
    "The scoring parameters are invalid, so the previous result was "
    "cleared. Fix the parameters, then run the decision analysis again."
)


def clear_if_parameters_invalid(
    result: DecisionPipelineResult | None,
    current_parameters: ScoringParameters | None,
) -> tuple[DecisionPipelineResult | None, str | None]:
    """Never keep a result on screen while the parameter editor is invalid.

    The run is disabled while the editor input is rejected, so no result
    can have been computed with those values. Retaining an older result
    next to the invalid sidebar would blur that provenance; clearing it
    keeps the displayed/current parameter relationship explicit.
    """
    if result is not None and current_parameters is None:
        return None, INVALID_PARAMETERS_MESSAGE
    return result, None


# -- scoring-parameter editor (construction only; the model validates) ------------


def _parameter(value: float, name: str) -> float:
    """Coerce one editor field; empty text means 'not provided'."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"{name} is required")
        value = float(text)
    return value


def build_scoring_parameters(
    schedule_pressure_weight: float,
    successor_impact_weight: float,
    consequence_of_delay_weight: float,
    successor_cap: int,
    horizon: float | None = None,
) -> tuple[ScoringParameters | None, str | None]:
    """Build the parameters the workflow will pass to ``run_decision``.

    Validation is NOT duplicated: we hand the values to the existing
    ``ScoringParameters`` model and surface its rejection verbatim
    (weights must sum to 1.0 and are never silently normalized).
    """
    try:
        parameters = ScoringParameters(
            schedule_pressure_weight=_parameter(schedule_pressure_weight, "schedule pressure weight"),
            successor_impact_weight=_parameter(successor_impact_weight, "successor impact weight"),
            consequence_of_delay_weight=_parameter(consequence_of_delay_weight, "consequence weight"),
            successor_cap=int(successor_cap),
            horizon=None if horizon in (None, "") else float(horizon),
        )
    except (ValueError, TypeError) as exc:
        return None, str(exc)
    return parameters, None


# -- workflow-progress stepper (state-aware presentation only) -------------------

_STEPS = ("Project", "Planning Period", "Candidate Activities", "Decision Setup", "Dashboard")

_STEP_DONE = "done"
_STEP_ACTIVE = "active"
_STEP_TODO = "todo"


def workflow_steps(
    workflow: DecisionWorkflowState | None, has_result: bool
) -> list[tuple[str, str]]:
    """Where the user is, derived ONLY from the workflow state."""
    done = [
        workflow is not None,
        workflow is not None and workflow.planning_session is not None,
        workflow is not None and workflow.planning_session is not None and
        len(workflow.planning_session.candidate_task_ids) > 0,
        workflow is not None and workflow.confirmed,
        has_result,
    ]
    states: list[tuple[str, str]] = []
    first_open = True
    for label, is_done in zip(_STEPS, done):
        if is_done:
            states.append((label, _STEP_DONE))
        elif first_open:
            states.append((label, _STEP_ACTIVE))
            first_open = False
        else:
            states.append((label, _STEP_TODO))
    return states


# -- result transformation (display rows only) -----------------------------------


def project_summary_rows(workflow: DecisionWorkflowState) -> list[dict]:
    """Project facts exactly as captured; unavailable stays '—'."""
    state = workflow.project_state
    first_date, last_date = default_period_bounds(state)
    return [
        {"Fact": "Project name", "Value": optional_text(state.project_name)},
        {"Fact": "Tasks", "Value": optional_text(len(state.tasks))},
        {"Fact": "Resources", "Value": optional_text(len(state.resources))},
        {
            "Fact": "Project date range",
            "Value": f"{date_text(first_date)} to {date_text(last_date)}",
        },
        {
            "Fact": "Captured at",
            "Value": f"{state.captured_at:%Y-%m-%d %H:%M}" if state.captured_at is not None else _NOT_AVAILABLE,
        },
    ]


def default_period_bounds(state: ProjectState) -> tuple[date | None, date | None]:
    """A sensible period suggestion from project dates (UI default only)."""
    starts = [task.start for task in state.tasks if task.start is not None]
    finishes = [task.finish for task in state.tasks if task.finish is not None]
    # Missing dates never block: either bound may legitimately be None.
    if not starts:
        return None, None
    return min(starts), max(finishes)


def candidate_rows(
    state: ProjectState, session, default_ids: frozenset[str] | None = None
) -> list[dict]:
    """Candidate management rows, in deterministic candidate order."""
    by_id = {task.id: task for task in state.tasks}
    if default_ids is None:
        from project_decision.application.candidate_discovery import (
            discover_default_candidates,
        )

        default_ids = frozenset(discover_default_candidates(state, session.planning_period))
    rows: list[dict] = []
    for task_id in session.candidate_task_ids:
        task = by_id[task_id]
        rows.append(
            {
                "Task ID": task.id,
                "Name": task.name,
                "Start": date_text(task.start),
                "Finish": date_text(task.finish),
                "Progress": criterion_text(task.progress),
                "Critical": optional_text(task.is_critical),
                "Slack": slack_text(task.total_slack_days),
                "Source": "discovered" if task_id in default_ids else "manually added",
            }
        )
    return rows


def ranking_rows(result: DecisionPipelineResult) -> list[dict]:
    """The priority ranking table, already in rank order (no re-ranking)."""
    rows: list[dict] = []
    for ranked in result.decision_result.ranked_tasks:
        rows.append(
            {
                "Rank": ranked.rank,
                "Activity": f"{ranked.task_name} ({ranked.task_id})",
                "Score": score_text(ranked.total_score),
                "Schedule Pressure": criterion_text(ranked.schedule_pressure),
                "Successor Impact": criterion_text(ranked.successor_impact),
                "Consequence": criterion_text(ranked.consequence_of_delay),
                "Critical": optional_text(ranked.is_critical),
                "Review": review_text(ranked),
                "Resource": (
                    ", ".join(ranked.shared_resource_names)
                    if ranked.resource_warning
                    else _NOT_AVAILABLE
                ),
                "Status": row_state(ranked),
            }
        )
    return rows


def top_score(ranked_tasks: tuple[RankedTask, ...]) -> float | None:
    """The highest known priority score (None when nothing is scored)."""
    return max(
        (one.total_score for one in ranked_tasks if one.total_score is not None),
        default=None,
    )


def contribution_rows(
    ranked: RankedTask, parameters: ScoringParameters
) -> list[dict]:
    """One row per criterion: value, weight, contribution (as produced)."""
    score = ranked.score
    if score is None:
        return []
    value_weight_contribution = (
        ("Schedule Pressure", score.schedule_pressure,
         parameters.schedule_pressure_weight, score.schedule_contribution),
        ("Successor Impact", score.successor_impact,
         parameters.successor_impact_weight, score.successor_contribution),
        ("Consequence of Delay", score.consequence_of_delay,
         parameters.consequence_of_delay_weight, score.consequence_contribution),
    )
    return [
        {
            "Criterion": name,
            "Value": criterion_text(value),
            "Weight": f"{weight:.2f}",
            "Contribution": score_text(contribution),
        }
        for name, value, weight, contribution in value_weight_contribution
    ]


def evaluation_details(result: DecisionPipelineResult, task_id: str) -> dict | None:
    """The stored JEV evaluation for one task (score/confidence/legend/probs)."""
    evaluations = result.consequence_evaluations
    if evaluations is None:
        return None
    if isinstance(evaluations, Mapping):
        evaluation = evaluations.get(task_id)
    else:
        evaluation = next(
            (one for one in evaluations if one.task_id == task_id), None
        )
    if evaluation is None:
        return None
    return {
        "status": evaluation.status,
        "score": evaluation.jev_score,
        "confidence": evaluation.confidence,
        "probabilities": evaluation.probabilities,
        "legend": evaluation.legend,
        "error_message": evaluation.error_message,
        "error_kind": evaluation.error_kind,
    }


def conflict_details(result: DecisionPipelineResult, task_id: str) -> list[dict]:
    """Existing CONFLICT-analysis facts for one task (no recomputation)."""
    rows: list[dict] = []
    for conflict in result.conflict_analysis.conflicts_with_status(
        ConflictStatus.CONFLICT
    ):
        if task_id not in conflict.task_ids:
            continue
        other = next(other for other in conflict.task_ids if other != task_id)
        if conflict.overlap_start is not None and conflict.overlap_finish is not None:
            overlap = (
                f"{conflict.overlap_start.isoformat()} to "
                f"{conflict.overlap_finish.isoformat()}"
            )
        else:
            overlap = "not determined"
        rows.append(
            {
                "Other task": other,
                "Shared resources": ", ".join(conflict.resource_names),
                "Overlap": overlap,
                "Explanation": conflict.explanation,
            }
        )
    return rows


def successor_details(
    result: DecisionPipelineResult,
    state: ProjectState,
    task_id: str,
) -> dict | None:
    """Successor identities from the stored ScheduleAnalysis (no re-analysis).

    Names are looked up in the project snapshot the pipeline consumed;
    an unknown id stays unknown, nothing is fabricated.
    """
    schedule = result.schedule_analysis.schedules.get(task_id)
    if schedule is None:
        return None
    names_by_id = {task.id: task.name for task in state.tasks}
    return {
        "slack_days": schedule.total_slack_days,
        "is_critical": schedule.is_critical,
        "successor_count": schedule.successor_count,
        "successor_ids": schedule.successor_ids,
        "successor_names": tuple(
            names_by_id.get(successor_id, _NOT_AVAILABLE)
            for successor_id in schedule.successor_ids
        ),
    }


UNSCORED_DETAIL_REASON = (
    "this candidate has no score (unscored); it requires manager review"
)


def detail_review_reasons(ranked: RankedTask) -> tuple[str, ...]:
    """Review reasons for the task-detail panel.

    A candidate without a score is NEVER presented as a clean task: it is
    unscored and therefore review-required, even though there may be no
    per-criterion reasons to list.
    """
    if ranked.score is None:
        return (UNSCORED_DETAIL_REASON,)
    return tuple(ranked.review_reasons)


def review_center_rows(result: DecisionPipelineResult) -> list[dict]:
    """Compact review summary from the EXISTING review fields only."""
    review: DecisionReview = result.decision_review
    rows: list[dict] = []

    def add(issue: str, detail: str) -> None:
        rows.append({"Issue": issue, "Detail": detail})

    if review.tasks_needing_review:
        add(
            "Needs review",
            f"{len(review.tasks_needing_review)} task(s): "
            f"{', '.join(review.tasks_needing_review)}",
        )
    if review.provisional_consequence_count:
        add(
            "Provisional consequence",
            f"{review.provisional_consequence_count} task(s) with a neutral "
            "0.5 consequence of delay (JEV off/unavailable) — flagged, "
            "not ranked on the invented value.",
        )
    if review.low_confidence_consequence_count:
        add(
            "Low consequence confidence",
            f"{review.low_confidence_consequence_count} task(s) below the "
            "0.5 confidence threshold — value used, review recommended.",
        )
    if review.tasks_with_missing_schedule:
        add(
            "Missing schedule data",
            f"{len(review.tasks_with_missing_schedule)} task(s): "
            f"{', '.join(review.tasks_with_missing_schedule)} "
            "(schedule pressure unavailable).",
        )
    failed = _failed_evaluation_ids(result)
    if failed:
        add(
            "JEV evaluation failed / unavailable",
            f"{len(failed)} task(s): {', '.join(failed)} — structured "
            "failures kept in the result, consequence provisional.",
        )
    if review.tasks_with_resource_warnings:
        add(
            "Resource warnings",
            f"{len(review.tasks_with_resource_warnings)} task(s): "
            f"{', '.join(review.tasks_with_resource_warnings)} — warnings "
            "never change scores or ranks.",
        )
    if review.unscored_task_ids:
        add(
            "Unscored candidates",
            f"{len(review.unscored_task_ids)} task(s): "
            f"{', '.join(review.unscored_task_ids)} — ranked last without "
            "a number, never a fabricated score.",
        )
    return rows


def _failed_evaluation_ids(result: DecisionPipelineResult) -> tuple[str, ...]:
    evaluations = result.consequence_evaluations
    if evaluations is None:
        return ()
    if isinstance(evaluations, Mapping):
        items = evaluations.items()
    else:
        items = ((evaluation.task_id, evaluation) for evaluation in evaluations)
    return tuple(
        task_id for task_id, evaluation in items
        if evaluation.status in (STATUS_FAILED, STATUS_UNAVAILABLE)
    )


# -- error messages (WHAT happened / WHY it matters / WHAT to do next) -----------


def msp_error(exc: Exception) -> str:
    """A structured read-only loading error message (never a stack trace)."""
    from project_decision.infrastructure.msp.exceptions import (
        MspNoActiveProjectError,
        MspNotAvailableError,
    )

    if isinstance(exc, MspNoActiveProjectError):
        return (
            "**WHAT:** Microsoft Project could not be read because no "
            "project is active.\n\n"
            "**WHY IT MATTERS:** the analysis needs the current schedule "
            "snapshot; nothing is fabricated in its place.\n\n"
            "**NEXT:** open a project in Microsoft Project, then press "
            "**Load Active MSP Project** again. Nothing was written, "
            "saved, or closed."
        )
    if isinstance(exc, MspNotAvailableError):
        return (
            "**WHAT:** Microsoft Project is not reachable through COM "
            "right now.\n\n"
            "**WHY IT MATTERS:** without the live snapshot the planner "
            "cannot analyze the current schedule.\n\n"
            "**NEXT:** make sure Microsoft Project is running, that "
            "pywin32 is installed (`pip install pywin32`), then try "
            "again."
        )
    return (
        f"**WHAT:** the active Microsoft Project could not be read "
        f"({exc}).\n\n"
        "**WHY IT MATTERS:** the analysis requires a complete, honest "
        "project snapshot.\n\n"
        "**NEXT:** reopen or repair the project in Microsoft Project, "
        "then try again. No partial or invented data was loaded."
    )


# -- factories (dependency injection for the workflow) ---------------------------


def build_project_loader() -> Callable[[], object]:
    """The real read-only MSP loader (the workflow consumes it as injected
    input; the MVP has exactly this one source)."""
    from project_decision.application.project_loader import load_active_project

    return lambda: load_active_project()


def build_demo_loader() -> Callable[[], object]:
    """The offline Demo Mode loader (no MSP, no COM, no JEV, no network).

    Same workflow boundary as ``build_project_loader`` — only the
    source differs. The demo fixture is deterministic data, so a demo
    run is always reproducible.
    """
    from project_decision.demo import load_demo_project

    return load_demo_project


def build_offline_evaluator():
    """A consequence evaluator that evaluates NOTHING (Jev off).

    Honest: scoring then uses its own provisional neutral CD and flags
    review — nothing is invented, the UI stays fully usable offline.
    """
    return lambda session, state: None


def build_jev_evaluator(env_file_directory=None):
    """The real consequence evaluator, or None when JEV is not configured.

    Returns (evaluator, note); the note explains offline fallback. The
    API key itself is NEVER returned or displayed.
    """
    from project_decision.application.consequence_evaluation import (
        evaluate_session_consequences,
    )
    from project_decision.config import load_typesafe_settings

    settings = load_typesafe_settings(env_file_directory=env_file_directory)
    if settings.api_key is None:
        return None, (
            "LIVE MODE unavailable: no API key is configured, so JEV "
            "cannot evaluate consequences. Continuing in OFFLINE MODE "
            "(provisional consequences, flagged for review)."
        )
    return (
        lambda session, state: evaluate_session_consequences(
            session, state, settings=settings, env_file_directory=env_file_directory
        ),
        None,
    )


# -- small aggregates the dashboard reads from the existing review ---------------


# -- sample MSP project (discoverable in the UI) ---------------------------------


def sample_project_path() -> str:
    """Absolute path of the bundled sample `.mpp` (text only; no COM).

    Helper for the UI's "Sample MSP Project" expander: shows WHERE the
    file is so the user can double-click it in Microsoft Project. The
    application itself never opens or writes the file.
    """
    from pathlib import Path

    return str(
        Path(__file__).resolve().parents[2]
        / "examples"
        / "production-line-commissioning"
        / "Production_Line_Commissioning.mpp"
    )


def sample_project_guide_text() -> str:
    """What the UI tells the user about the bundled sample MSP project."""
    path = sample_project_path()
    return (
        "**Sample MSP Project** — a real Microsoft Project file "
        "(~120 tasks, 12 phases, 10 resources, industrial commissioning "
        "scenario) bundled with the repository in\n"
        f"`{path}`.\n\n"
        "**How to use it**\n"
        "1. Open that `.mpp` file in Microsoft Project on this machine.\n"
        "2. Keep it the ACTIVE project window there.\n"
        "3. Come back here and press **Load Active MSP Project** — the "
        "integration reads it strictly read-only.\n"
        "4. Pick a planning period (a recommended first window is "
        "`2026-10-22 → 2026-11-06`), confirm, and run the analysis.\n\n"
        "**What it is and is not**\n"
        "- It is NOT the Demo Mode (which runs entirely offline).\n"
        "- THIS sample uses the live Microsoft Project integration, so it "
        "needs Microsoft Project installed and a project open.\n"
        "- Consequence of Delay (JEV) stays offline (provisional) unless "
        "a `TYPESAFE_API_KEY` is configured; nothing pretend is loaded.\n"
        "- No file is opened, saved, or closed by this application.\n\n"
        "Full guide and verified expected results live next to the file in "
        "`examples/production-line-commissioning/`."
    )


def review_reason_chips(review: DecisionReview) -> list[str]:
    """Machine counts from the existing review, as display chips."""
    return [
        f"{reason.replace('_', ' ')}: {count}"
        for reason, count in sorted(review.review_reason_counts.items())
    ]
