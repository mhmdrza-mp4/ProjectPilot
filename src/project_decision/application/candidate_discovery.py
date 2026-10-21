"""Default candidate discovery: which tasks go into the planning funnel.

This answers, deterministically, "which activities does the system
suggest as candidates for the planning period?" — the STARTING point a
manager adjusts. It performs no scoring, ranking, or optimization: it
applies fixed, documented rules and never invents data.

Default-candidate rules (all must hold):
1. the task is NOT a summary task (``is_summary`` is the MSP Summary
   flag — never inferred from names, indentation, duration, or deps);
2. progress < 100 % (completed work needs no planning decision);
3. the task has BOTH a start and a finish date (missing dates are never
   inferred — an undated task is unknowably schedulable, so it is not
   suggested);
4. the date range overlaps the period inclusively:
   ``task.start <= period.end_date and task.finish >= period.start_date``.
   Boundary tasks count as overlapping: a task started before the period
   but still active during it, and a task starting inside and finishing
   after it, are both included; tasks entirely before or after are not.

The result is ordered by the state's task order, so repeated discovery
on the same state is identical.
"""

from __future__ import annotations

from project_decision.domain.planning_period import PlanningPeriod
from project_decision.domain.project_state import ProjectState


def discover_default_candidates(
    state: ProjectState, period: PlanningPeriod
) -> tuple[str, ...]:
    """Return the default candidate task ids for the planning period."""
    candidates: list[str] = []
    for task in state.tasks:
        if task.is_summary:
            continue
        if task.progress >= 1.0:
            continue
        if task.start is None or task.finish is None:
            continue
        if task.start <= period.end_date and task.finish >= period.start_date:
            candidates.append(task.id)
    return tuple(candidates)
