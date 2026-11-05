"""Schedule analysis: relationships between the tasks of one captured state.

Scope (deliberately small — analysis, not scheduling):
- derives successor relationships purely from each task's
  ``dependency_ids`` (the stored predecessors). It answers "which tasks
  depend on which?", as input to later prioritization work. Two views
  are exposed per task: the DIRECT successors, and the whole DOWNSTREAM
  set (direct + indirect, i.e. everything that transitively waits for
  this task). The downstream count is a pure graph property of the same
  dependency data — nothing is scheduled or estimated.
- reports per task the source schedule fields exactly as captured
  (``total_slack_days``, ``is_critical``). Nothing is derived here:
  this layer does NOT calculate criticality from slack, slack from
  criticality, or any other schedule quantity — Microsoft Project
  remains the scheduling engine. ``None`` = the source did not report
  the value, never an invented default.

Data-quality handling:
- Unlike ``Project`` (which validates references at construction),
  ``ProjectState`` is a snapshot and does not enforce referential
  integrity — snapshots loaded from JSON may contain broken references.
  Unknown dependency ids, duplicated dependency ids, and self-references
  are therefore reported as issues; they never create fake tasks and
  never abort the analysis.
- Duplicate dependency ids collapse: a target appears in a successor
  list at most once.
- Dependency cycles (impossible in Microsoft Project, but possible in a
  hand-edited JSON snapshot) never hang the analysis: every traversal
  tracks visited tasks, a task is never counted as its own downstream,
  and each task that lies on a cycle is reported as an issue.
- Task ids are treated as unique within a state, which is the invariant
  ``Project`` enforces at construction; snapshots retain it on round-trip.
"""

from __future__ import annotations

from dataclasses import dataclass

from project_decision.domain.project_state import ProjectState


@dataclass(frozen=True)
class TaskSchedule:
    """Per-task schedule view: relationships plus the captured schedule fields.

    ``successor_count == 0`` is a real count (no other task depends on
    this one) and stays distinguishable from unavailable data, which is
    always an explicit ``None`` field.

    ``successor_count`` counts DIRECT successors only; ``downstream_count``
    counts every task that transitively depends on this one (direct and
    indirect, each task once, never the task itself). ``analyze_schedule``
    always provides it. When a ``TaskSchedule`` is built by hand without
    it, it defaults to the direct count — a true lower bound, exact
    whenever no successor has successors of its own.
    """

    task_id: str
    successor_ids: tuple[str, ...]
    successor_count: int
    total_slack_days: float | None
    is_critical: bool | None
    downstream_count: int | None = None

    def __post_init__(self) -> None:
        if self.downstream_count is None:
            object.__setattr__(self, "downstream_count", self.successor_count)


@dataclass(frozen=True)
class ScheduleAnalysis:
    """Relationship structure of one captured state.

    ``schedules`` covers every task in the state, keyed by task id.
    ``issues`` holds the data-quality problems found in dependency lists
    (unknown ids, duplicates, self-references), deterministically ordered
    by the state's task order; a clean state yields an empty tuple.
    """

    schedules: dict[str, TaskSchedule]
    issues: tuple[str, ...]


def analyze_schedule(state: ProjectState) -> ScheduleAnalysis:
    """Derive successor relationships from the captured dependencies."""
    successors: dict[str, list[str]] = {task.id: [] for task in state.tasks}
    issues: list[str] = []

    for task in state.tasks:
        seen_dependency_ids: set[str] = set()
        for dependency_id in task.dependency_ids:
            if dependency_id in seen_dependency_ids:
                issues.append(
                    f"task '{task.id}' lists dependency '{dependency_id}' "
                    "more than once"
                )
                continue
            seen_dependency_ids.add(dependency_id)
            if dependency_id == task.id:
                # A task is not its own successor; the self-reference
                # is a data problem, not a relationship.
                issues.append(f"task '{task.id}' depends on itself")
                continue
            if dependency_id not in successors:
                issues.append(
                    f"task '{task.id}' depends on unknown task '{dependency_id}'"
                )
                continue
            successors[dependency_id].append(task.id)

    downstream_counts, cyclic_task_ids = _downstream_counts(successors)
    for task in state.tasks:
        if task.id in cyclic_task_ids:
            issues.append(f"task '{task.id}' is part of a dependency cycle")

    return ScheduleAnalysis(
        schedules={
            task.id: TaskSchedule(
                task_id=task.id,
                successor_ids=tuple(successors[task.id]),
                successor_count=len(successors[task.id]),
                total_slack_days=task.total_slack_days,
                is_critical=task.is_critical,
                downstream_count=downstream_counts[task.id],
            )
            for task in state.tasks
        },
        issues=tuple(issues),
    )


def _downstream_counts(
    successors: dict[str, list[str]],
) -> tuple[dict[str, int], set[str]]:
    """Count every task reachable from each task through successor links.

    Iterative traversal (no recursion limit on long chains) with a
    visited set per start task, so shared sub-chains and cycles are safe:
    each downstream task is counted once, and a start task reached again
    through a cycle is NOT counted as its own downstream but is reported
    in the returned set of cyclic task ids.
    """
    counts: dict[str, int] = {}
    cyclic: set[str] = set()
    for start in successors:
        seen: set[str] = set()
        stack = list(successors[start])
        while stack:
            node = stack.pop()
            if node == start:
                cyclic.add(start)
                continue
            if node in seen:
                continue
            seen.add(node)
            stack.extend(successors[node])
        counts[start] = len(seen)
    return counts, cyclic
