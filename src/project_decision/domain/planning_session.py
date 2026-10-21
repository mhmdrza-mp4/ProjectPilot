"""PlanningSession concept: one manager's selected activities for one period.

A session is a thin, immutable record over the default candidate set:

    Default Candidate Set (candidate_discovery)
            ↓  manager may add/remove
    Final Candidate Set
            ↓
    PlanningSession (this concept)

Deliberate boundaries:
- a session holds ONLY the period and the selected task ids; it contains
  no priority score, rank, JEV answer, confidence, or weight — scoring
  and ranking belong to later pipeline stages, so those concepts stay
  out by design;
- the session references tasks by id only; it never copies or mutates
  the ProjectState it was created from, and add/remove return NEW
  sessions instead of editing one in place;
- sessions do NOT validate that ids exist in some state (the state is
  not part of the session); existence/duplicate validation for the
  add/remove operations lives in application/planning_session.py, and
  duplicate ids are rejected here as well.
"""

from __future__ import annotations

from dataclasses import dataclass

from project_decision.domain.planning_period import PlanningPeriod


@dataclass(frozen=True)
class PlanningSession:
    """Selected candidate task ids for one planning period."""

    planning_period: PlanningPeriod
    candidate_task_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if any(not isinstance(task_id, str) or not task_id for task_id in self.candidate_task_ids):
            raise ValueError("PlanningSession candidate task ids must be non-empty strings")
        if len(set(self.candidate_task_ids)) != len(self.candidate_task_ids):
            raise ValueError("PlanningSession candidate task ids must be unique")
