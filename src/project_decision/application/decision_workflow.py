"""Decision workflow: the product-level flow the future UI depends on.

    load_project → ProjectState
        ↓ set_planning_period (default candidates via existing discovery)
    PlanningSession → confirm → run_decision
        ↓ run_decision_pipeline (the existing one — nothing reused
    DecisionPipelineResult

Deliberate boundaries (pure orchestration, ZERO business logic):
- every operation is an immutable transition: the given
  ``DecisionWorkflowState`` (and the ``ProjectState`` inside it) is
  never mutated — each function returns a NEW workflow state;
- candidate discovery/editing delegates to the existing
  ``candidate_discovery``/``planning_session`` functions; no second
  selection or session-mutation implementation exists;
- the decision run delegates to the existing
  ``run_decision_pipeline`` with an INJECTED consequence evaluator and
  optional ``ScoringParameters``; schedule analysis, conflicts, JEV,
  scoring, ranking and review are never called from here;
- confirmation is a lightweight UI-state flag, not a permission
  system: any edit (period or candidates) un-confirms the workflow,
  so a run always reflects a deliberately confirmed set;
- result invalidation is structural, not cached: results are never
  compared/updated incrementally — ``result_is_stale`` reports whether
  a DecisionPipelineResult was computed for exactly the CURRENT
  session (period or candidate changes change the session, hence
  invalidate); the next run always executes the whole pipeline again;
- errors stay errors: missing session, unconfirmed session, unknown
  task, duplicate candidate and removing a non-candidate surface as
  the existing ValueError/domain messages; infrastructure load errors
  (``MspError``) propagate; programming errors are never caught;
- UI independence: importing this module pulls in domain +
  application only — no Streamlit, FastAPI, COM, TypeSafe SDK, or
  database libraries.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from project_decision.application.decision_pipeline import (
    ConsequenceEvaluator,
    DecisionPipelineResult,
    run_decision_pipeline,
)
from project_decision.application.planning_session import (
    add_candidate as _add_candidate,
)
from project_decision.application.planning_session import (
    remove_candidate as _remove_candidate,
)
from project_decision.application.priority_scoring import ScoringParameters
from project_decision.domain.planning_period import PlanningPeriod
from project_decision.domain.planning_session import PlanningSession
from project_decision.domain.project_state import ProjectState

# Injectable loader: any callable returning a ProjectState, e.g.
# ``lambda: project_loader.load_project(adapter, captured_at)``,
# ``lambda: project_loader.load_active_project()``, or a fake in tests.
ProjectLoader = Callable[[], ProjectState]


@dataclass(frozen=True)
class DecisionWorkflowState:
    """One user-visible workflow position (minimal, immutable).

    Only pointers to EXISTING models — nothing is duplicated: the
    loaded ``ProjectState``, the ``PlanningPeriod``, the current
    ``PlanningSession`` (None until the period is set), and the
    confirmation flag. Period/candidate editing always produces a NEW
    session, which is what keeps prior decision results stale (see
    ``result_is_stale``).
    """

    project_state: ProjectState
    planning_period: PlanningPeriod | None = None
    planning_session: PlanningSession | None = None
    confirmed: bool = False


def load_project(loader: ProjectLoader) -> DecisionWorkflowState:
    """Load the project via the injected loader and open the workflow.

    ``loader`` is called exactly once and must return a ``ProjectState``
    (the application loader's ``load_project``/``load_active_project``
    bound with its configuration, or a fake). Loading failures — e.g.
    ``MspError`` — propagate unchanged; they are never converted into
    an empty workflow.
    """
    return DecisionWorkflowState(project_state=loader())


def set_planning_period(
    state: DecisionWorkflowState, period: PlanningPeriod
) -> DecisionWorkflowState:
    """Set (or replace) the planning period and create the session.

    The period's own rules stay authoritative (``start <= end``;
    invalid ranges raise the domain ValueError). The session comes from
    the existing ``create_planning_session`` — i.e. the existing default
    candidate discovery (non-summary, not finished, dated, overlapping
    the period) — and any previous confirmation is reset. Empty results
    are valid; a session with zero candidates can run.
    """
    from project_decision.application.planning_session import (
        create_planning_session,
    )

    return _next(
        state,
        planning_period=period,
        planning_session=create_planning_session(state.project_state, period),
    )


def add_candidate(
    state: DecisionWorkflowState, task_id: str
) -> DecisionWorkflowState:
    """Return the workflow state with one candidate added.

    Delegates to the existing immutable session operation: the task
    must exist in the state (ValueError otherwise), duplicates are
    rejected, manually added tasks bypass the default rules on
    purpose. Editing un-confirms the workflow.
    """
    return _next(
        state,
        planning_session=_add_candidate(
            state.project_state, _session_of(state), task_id
        ),
    )


def remove_candidate(
    state: DecisionWorkflowState, task_id: str
) -> DecisionWorkflowState:
    """Return the workflow state with one candidate removed.

    Delegates to the existing immutable session operation; removing an
    id that is not a candidate raises the existing explicit ValueError.
    Editing un-confirms the workflow.
    """
    return _next(
        state,
        planning_session=_remove_candidate(_session_of(state), task_id),
    )


def confirm_session(state: DecisionWorkflowState) -> DecisionWorkflowState:
    """Mark the current session as deliberately confirmed.

    Confirmation changes NOTHING except this flag: project state,
    period, and session are carried over unchanged. Any later edit
    resets it (see ``add_candidate``/``remove_candidate``/
    ``set_planning_period``).
    """
    _session_of(state)  # confirming nothing is a programming slip
    return _next(state, confirmed=True)


def run_decision(
    state: DecisionWorkflowState,
    consequence_evaluator: ConsequenceEvaluator,
    scoring_parameters: ScoringParameters | None = None,
) -> DecisionPipelineResult:
    """Run the existing decision pipeline for the confirmed session.

    Delegates COMPLETELY to ``run_decision_pipeline`` (schedule
    analysis → conflicts → consequence evaluation → scoring → ranking
    → review); the injected evaluator and scoring parameters arrive
    there unchanged. Expected evaluation failures stay inside the
    structured result models; unexpected errors propagate.

    Raises:
        ValueError: no session yet, or the session was not confirmed.
    """
    session = _session_of(state)
    if not state.confirmed:
        raise ValueError(
            "the planning session is not confirmed; confirm it before "
            "running the decision"
        )
    return run_decision_pipeline(
        state.project_state,
        session,
        consequence_evaluator,
        scoring_parameters=scoring_parameters,
    )


def result_is_stale(
    state: DecisionWorkflowState, result: DecisionPipelineResult | None
) -> bool:
    """Whether ``result`` was NOT computed for exactly this workflow state.

    The simple invalidation representation: period or candidate changes
    produce a NEW planning session, so any result computed from a
    previous session (or None) is stale and the pipeline must run
    again. Nothing is cached and nothing is incrementally updated.
    """
    return result is None or result.planning_session != state.planning_session


# -- internal -------------------------------------------------------------------


def _session_of(state: DecisionWorkflowState) -> PlanningSession:
    if state.planning_session is None:
        raise ValueError(
            "no planning session; set a planning period first"
        )
    return state.planning_session


def _next(
    state: DecisionWorkflowState,
    **overrides: object,
) -> DecisionWorkflowState:
    """Immutable transition helper: keep or replace fields, never confirmed
    through edits (overwrite un-confirms)."""
    fields = {
        "project_state": state.project_state,
        "planning_period": state.planning_period,
        "planning_session": state.planning_session,
        "confirmed": False,  # any transition through an edit un-confirms
    }
    fields.update(overrides)
    return DecisionWorkflowState(
        project_state=fields["project_state"],
        planning_period=fields["planning_period"],
        planning_session=fields["planning_session"],
        confirmed=fields["confirmed"],
    )
