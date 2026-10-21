"""Resource-conflict analysis: which scheduled tasks may compete for a resource.

Scope (deliberately small — analysis/warning, not resource leveling):
- a potential conflict exists when two tasks share at least one assigned
  resource AND their scheduled date ranges share at least one common day.
  This layer answers only "which scheduled tasks may compete for the same
  resource during overlapping dates"; it does NOT level resources,
  re-schedule, or decide anything.

Conflict definition (documented, deliberate):
- ``start``/``finish`` are plain calendar dates; a task occupies its
  scheduled [start .. finish] days, both endpoints inclusive. Two ranges
  overlap when ``max(starts) <= min(finishes)``. The boundary day counts:
  a task finishing 2026-01-15 and one starting 2026-01-15 share Jan 15.
- Task pairs are examined in state task order and a pair produces at most
  ONE result, aggregating every (known) resource the two tasks share — no
  duplicate pair entries per shared resource.
- A disjoint pair (both dates known, no common day) produces no result at
  all: non-overlap is provable, not "unknown".

What capacity can and cannot tell us (no invented allocation data):
- ``Resource.capacity_units`` states how many units exist, but tasks carry
  NO per-assignment demand (no units consumed per task). A capacity
  overload ("3 tasks on capacity 2") therefore cannot be computed —
  attempting it would require invented assumptions (one task = one unit,
  full capacity per assignment, equal distribution, ...). The analysis
  reports the time overlap only and states this limitation explicitly.

Missing data:
- dates are never fabricated. If either task of a resource-sharing pair
  lacks a start or a finish, the pair's status is UNKNOWN and the
  explanation names every missing value — never a claimed or guessed
  overlap.

Data-quality handling (same conventions as schedule_analysis):
- ``ProjectState`` does not enforce referential integrity; unknown
  resource ids, duplicated resource ids, and unusable capacity values are
  reported as issues. Unknown ids are not treated as shared resources (a
  reference to a nonexistent resource creates no conflict, and missing
  Resource objects are never fabricated).
- Task ids are treated as unique within a state (the invariant
  ``Project`` enforces at construction); a state containing duplicate
  resource entries would map each id to its last occurrence, since the
  domain never produces such states.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from project_decision.domain.project_state import ProjectState
from project_decision.domain.task import Task


class ConflictStatus(Enum):
    """Reliability status of one task-pair finding."""

    CONFLICT = "conflict"      # provably sharing at least one scheduled day
    UNKNOWN = "unknown"        # sharing resource(s); dates insufficient to decide


@dataclass(frozen=True)
class ResourceConflict:
    """One resource-sharing task pair, with what the data can support.

    ``status == CONFLICT``: the date ranges provably share days;
    ``overlap_start``/``overlap_finish`` delimit the common days and the
    shared resource ids/names are listed once for the whole pair.
    ``status == UNKNOWN``: the tasks share resource(s) but scheduling
    data is insufficient to determine overlap (or its absence);
    ``overlap_start``/``overlap_finish`` are None and ``explanation``
    names exactly which values are missing.
    Capacity overload is not part of either status — see module docstring.
    """

    task_ids: tuple[str, str]
    resource_ids: tuple[str, ...]
    resource_names: tuple[str, ...]
    status: ConflictStatus
    overlap_start: date | None = None
    overlap_finish: date | None = None
    explanation: str = ""


@dataclass(frozen=True)
class ResourceConflictAnalysis:
    """All resource-sharing task pairs of one captured state, plus issues.

    ``conflicts`` is ordered deterministically by state task order (pairs
    (t1, t2) with t1 before t2) and mixes CONFLICT and UNKNOWN results;
    ``status`` distinguishes them. ``issues`` lists data-quality problems
    (unknown/duplicate resource ids, unusable capacity) deterministically:
    task-reference issues in task order, then capacity issues in state
    resource order.
    """

    conflicts: tuple[ResourceConflict, ...]
    issues: tuple[str, ...]

    def conflicts_with_status(
        self, status: ConflictStatus
    ) -> tuple[ResourceConflict, ...]:
        """Deterministic view of one status, for later services."""
        return tuple(conflict for conflict in self.conflicts if conflict.status is status)


def analyze_resource_conflicts(state: ProjectState) -> ResourceConflictAnalysis:
    """Find resource-sharing task pairs and examine their scheduled dates."""
    resources_by_id = {resource.id: resource for resource in state.resources}
    resource_order = {resource.id: index for index, resource in enumerate(state.resources)}

    assignments: dict[str, list[str]] = {}
    issues: list[str] = []
    for task in state.tasks:
        assignments[task.id] = []
        seen_resource_ids: set[str] = set()
        for resource_id in task.assigned_resource_ids:
            if resource_id in seen_resource_ids:
                issues.append(
                    f"task '{task.id}' lists resource '{resource_id}' more than once"
                )
                continue
            seen_resource_ids.add(resource_id)
            if resource_id not in resources_by_id:
                issues.append(
                    f"task '{task.id}' references unknown resource '{resource_id}'"
                )
                continue
            assignments[task.id].append(resource_id)

    # Capacity data is validated at the domain (a float >= 0); this
    # defensive check covers only junk snapshots that bypass it.
    for resource_id in sorted(resources_by_id, key=resource_order.__getitem__):
        capacity = resources_by_id[resource_id].capacity_units
        if not _is_usable_number(capacity):
            issues.append(
                f"resource '{resource_id}' has an unusable capacity value "
                f"({capacity!r}); it is still counted as shared, but no "
                "capacity statement is possible"
            )

    conflicts: list[ResourceConflict] = []
    sharing_tasks = [task for task in state.tasks if assignments[task.id]]
    for index, first in enumerate(sharing_tasks):
        for second in sharing_tasks[index + 1 :]:
            conflict = _pair_result(first, second, assignments, resources_by_id, resource_order)
            if conflict is not None:
                conflicts.append(conflict)

    return ResourceConflictAnalysis(conflicts=tuple(conflicts), issues=tuple(issues))


def _pair_result(
    first: Task,
    second: Task,
    assignments: dict[str, list[str]],
    resources_by_id: dict,
    resource_order: dict[str, int],
) -> ResourceConflict | None:
    """One result per pair (None when the pair is provably not conflicting)."""
    second_ids = set(assignments[second.id])
    shared_ids = sorted(
        (rid for rid in assignments[first.id] if rid in second_ids),
        key=resource_order.__getitem__,
    )
    if not shared_ids:
        return None

    shared_names = tuple(resources_by_id[rid].name for rid in shared_ids)
    missing = _missing_dates(first, second)
    if missing:
        return ResourceConflict(
            task_ids=(first.id, second.id),
            resource_ids=tuple(shared_ids),
            resource_names=shared_names,
            status=ConflictStatus.UNKNOWN,
            explanation=missing,
        )

    overlap_start = max(first.start, second.start)
    overlap_finish = min(first.finish, second.finish)
    if overlap_start > overlap_finish:
        return None  # provably no common scheduled day
    shared_list = ", ".join(repr(name) for name in shared_names)
    return ResourceConflict(
        task_ids=(first.id, second.id),
        resource_ids=tuple(shared_ids),
        resource_names=shared_names,
        status=ConflictStatus.CONFLICT,
        overlap_start=overlap_start,
        overlap_finish=overlap_finish,
        explanation=(
            f"tasks '{first.id}' and '{second.id}' share resource(s) "
            f"{shared_list}; whether capacity is exceeded cannot be "
            "determined without assignment-level demand data"
        ),
    )


def _missing_dates(first: Task, second: Task) -> str | None:
    """Human-readable list of missing dates, in state task order."""
    missing: list[str] = []
    for task in (first, second):
        if task.start is None:
            missing.append(f"task '{task.id}' has no start date")
        if task.finish is None:
            missing.append(f"task '{task.id}' has no finish date")
    return "; ".join(missing) if missing else None


def _is_usable_number(value: object) -> bool:
    """True for real numeric capacity (bool is not accepted as a number)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)
