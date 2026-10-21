"""Planning-session use cases: create, add, remove — always immutable.

Flow: candidate_discovery proposes the default set; the manager shapes
the final set; these functions turn it into (and adjust) a
``PlanningSession``. Every function returns a NEW session; the given
``ProjectState`` is never mutated, and tasks are never removed from it.

Validation/conventions (matching the repository's style):
- unknown task id → ``ValueError("unknown task id: X")`` (Project.task's
  message shape); nothing is fabricated;
- duplicate candidate id → ``ValueError``, a candidate is never listed
  twice;
- removing an id that is not a candidate → ``ValueError`` (explicit, no
  silent no-op);
- a manually ADDED task only has to exist in the state — it may violate
  the default rules deliberately, so the manager can pull in an
  activity the rules did not suggest (e.g. already-finished-but-relevant).
"""

from __future__ import annotations

from project_decision.application.candidate_discovery import (
    discover_default_candidates,
)
from project_decision.domain.planning_period import PlanningPeriod
from project_decision.domain.planning_session import PlanningSession
from project_decision.domain.project_state import ProjectState


def create_planning_session(
    state: ProjectState,
    planning_period: PlanningPeriod,
    *,
    candidate_task_ids: tuple[str, ...] | list[str] | None = None,
) -> PlanningSession:
    """Create a session: default candidates unless explicit ids are given.

    With explicit ids NOTHING is silently filtered or reordered — every
    id must exist in the state and no id may repeat.
    """
    ids = (
        tuple(candidate_task_ids)
        if candidate_task_ids is not None
        else discover_default_candidates(state, planning_period)
    )
    _validate_candidate_ids(state, ids)
    return PlanningSession(
        planning_period=planning_period,
        candidate_task_ids=ids,
    )


def add_candidate(
    state: ProjectState,
    session: PlanningSession,
    task_id: str,
) -> PlanningSession:
    """Return a new session with the task added (right-most, no duplicates).

    The task must exist in the state; it does NOT have to satisfy the
    default-candidate rules — that is the point of manual addition.
    """
    _ensure_task_exists(state, task_id)
    if task_id in session.candidate_task_ids:
        raise ValueError(f"task '{task_id}' is already a candidate")
    return PlanningSession(
        planning_period=session.planning_period,
        candidate_task_ids=(*session.candidate_task_ids, task_id),
    )


def remove_candidate(session: PlanningSession, task_id: str) -> PlanningSession:
    """Return a new session without the task; the state is untouched."""
    if task_id not in session.candidate_task_ids:
        raise ValueError(f"task '{task_id}' is not a candidate of this session")
    return PlanningSession(
        planning_period=session.planning_period,
        candidate_task_ids=tuple(
            id_ for id_ in session.candidate_task_ids if id_ != task_id
        ),
    )


def _validate_candidate_ids(state: ProjectState, task_ids: tuple[str, ...]) -> None:
    """Every id must exist in the state; duplicates are rejected."""
    known = {task.id for task in state.tasks}
    for task_id in task_ids:
        if task_id not in known:
            raise ValueError(f"unknown task id: {task_id}")


def _ensure_task_exists(state: ProjectState, task_id: str) -> None:
    if task_id not in {task.id for task in state.tasks}:
        raise ValueError(f"unknown task id: {task_id}")
