"""Priority ranking: deterministic order + DecisionResult (no UI, no persistence).

Pipeline position:

    Priority Scores + ResourceConflictAnalysis
            ↓
    DecisionResult → RankedTask (rank 1, 2, 3, ...)

Deliberate boundaries:
- ranking consumes the numbers produced by ``priority_scoring``; it
  does NOT change, re-normalize, or re-interpret any score.
- resource conflicts NEVER move a rank or change a number; they are
  attached as warnings from the existing ``resource_conflicts.py``
  analysis (reused, not re-computed).
- review status (``needs_review``), criticality, and score ranges are
  preserved as explanation/display data; none of them affects order.
- ranking covers ONLY the session's candidates; scores for other tasks
  are ignored, and a candidate without a score is still listed — rank
  last, ``score=None`` — never dropped and never given a fake number.

Ranking rules (deterministic, SEQUENTIAL ranks; the final task-id
tie-break makes the order total, so competition ranking like 1, 1, 3
is never needed):
1. priority score ROUNDED TO ONE DECIMAL (``round(total, 1)``),
   higher first;
2. lower ``total_slack_days`` first (less slack = more urgent when
   scores tie);
3. higher direct successor count first;
4. higher EVALUATED Consequence of Delay first;
5. task id ascending (final stable break).

Missing values ("a known value is more informative than a missing
one") apply at every numeric tier:
- no total score, missing slack, or a missing schedule view sorts
  AFTER all known values at that tier — never a fabricated 0;
- a PROVISIONAL consequence (the neutral 0.5, not evaluated) counts as
  MISSING in the CD tier: ranking on the invented 0.5 would let a
  fabricated number decide order. Provisional tasks tie there and fall
  through to the next criterion.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from project_decision.application.priority_scoring import TaskPriorityScore
from project_decision.application.resource_conflicts import (
    ConflictStatus,
    ResourceConflictAnalysis,
)
from project_decision.application.schedule_analysis import ScheduleAnalysis, TaskSchedule
from project_decision.domain.planning_period import PlanningPeriod
from project_decision.domain.planning_session import PlanningSession
from project_decision.domain.project_state import ProjectState

_SCORE_DECIMALS = 1


@dataclass(frozen=True)
class RankedTask:
    """One ranked candidate: rank, the composed score, and display facts.

    Composition, not duplication: ``score`` IS the original
    ``TaskPriorityScore`` (None only for an unscored candidate); the
    convenience properties below simply expose its surface for the
    future dashboard. ``resource_warning`` comes from the existing
    conflict analysis (CONFLICT pairs only) and has ZERO influence on
    any number or rank printed here.
    """

    rank: int
    task_id: str
    task_name: str
    score: TaskPriorityScore | None
    total_slack_days: float | None
    successor_count: int | None
    is_critical: bool | None
    resource_warning: bool
    shared_resource_ids: tuple[str, ...] = ()
    shared_resource_names: tuple[str, ...] = ()

    # -- composed views (no copied data) ------------------------------------
    @property
    def total_score(self) -> float | None:
        return self.score.total_score if self.score is not None else None

    @property
    def schedule_pressure(self) -> float | None:
        return self.score.schedule_pressure if self.score is not None else None

    @property
    def successor_impact(self) -> float | None:
        return self.score.successor_impact if self.score is not None else None

    @property
    def consequence_of_delay(self) -> float | None:
        return self.score.consequence_of_delay if self.score is not None else None

    @property
    def consequence_source(self) -> str | None:
        return self.score.consequence_source if self.score is not None else None

    @property
    def criterion_contributions(self) -> tuple[float | None, float | None, float | None]:
        if self.score is None:
            return (None, None, None)
        return (
            self.score.schedule_contribution,
            self.score.successor_contribution,
            self.score.consequence_contribution,
        )

    @property
    def score_range(self):
        return self.score.score_range if self.score is not None else None

    @property
    def needs_review(self) -> bool:
        return self.score.needs_review if self.score is not None else True

    @property
    def review_reasons(self) -> tuple[str, ...]:
        return self.score.review_reasons if self.score is not None else ()

    def __str__(self) -> str:  # noqa: D105 - readable ranking line
        return (
            f"#{self.rank} {self.task_id} ({self.task_name!r}): "
            f"score={self.total_score} review={self.needs_review} "
            f"resource_warning={self.resource_warning}"
        )


@dataclass(frozen=True)
class DecisionResult:
    """The ranked decision result for one PlanningSession.

    ``ranked_tasks`` is the ordered answer (rank 1 first).
    ``candidate_count`` and ``tasks_with_resource_warnings`` are derived
    views, so a future UI can build the natural callout ("high-priority
    activities with resource conflicts") without new scoring rules.
    """

    planning_period: PlanningPeriod
    ranked_tasks: tuple[RankedTask, ...]

    @property
    def candidate_count(self) -> int:
        return len(self.ranked_tasks)

    def tasks_with_resource_warnings(self) -> tuple[RankedTask, ...]:
        """Ranked tasks carrying a resource-conflict warning, rank order."""
        return tuple(ranked for ranked in self.ranked_tasks if ranked.resource_warning)


def create_decision_result(
    session: PlanningSession,
    state: ProjectState,
    schedule_analysis: ScheduleAnalysis,
    scores: Sequence[TaskPriorityScore],
    conflict_analysis: ResourceConflictAnalysis | None = None,
) -> DecisionResult:
    """Rank the session's candidates into a deterministic DecisionResult.

    ``scores`` should be the output of the scoring layer (in any order);
    scores for tasks outside the session are ignored, and a candidate
    without a score is ranked last as honestly unscored (``score=None``).
    Nothing is mutated; everything returned is fresh, immutable data.
    """
    scores_by_id = {score.task_id: score for score in scores}
    tasks_by_id = {task.id: task for task in state.tasks}
    warnings = _resource_warnings(conflict_analysis)

    keyed: list[tuple[tuple, TaskPriorityScore | None, TaskSchedule | None, str]] = []
    for candidate_id in session.candidate_task_ids:
        schedule = schedule_analysis.schedules.get(candidate_id)
        score = scores_by_id.get(candidate_id)
        keyed.append((_ordering_key(candidate_id, schedule, score), score, schedule, candidate_id))
    keyed.sort(key=lambda item: item[0])

    ranked = tuple(
        _ranked_task(
            rank=index + 1,
            candidate_id=candidate_id,
            score=score,
            schedule=schedule,
            tasks_by_id=tasks_by_id,
            warnings=warnings,
        )
        for index, (_key, score, schedule, candidate_id) in enumerate(keyed)
    )
    return DecisionResult(planning_period=session.planning_period, ranked_tasks=ranked)


def _ordering_key(
    task_id: str,
    schedule: TaskSchedule | None,
    score: TaskPriorityScore | None,
) -> tuple:
    """Total order: rounded score, slack, successors, evaluated CD, task id.

    Higher-first values are negated; every tier encodes "known before
    missing" as ``(0, value)`` vs ``(1, 0.0)`` so None never has to be a
    fabricated zero. The trailing task id makes the order total.
    """
    total = score.total_score if score is not None else None
    slack = schedule.total_slack_days if schedule is not None else None
    successors = schedule.successor_count if schedule is not None else None
    cd_known = (
        score is not None
        and score.consequence_source == "evaluated"
    )
    return (
        (1, 0.0) if total is None else (0, -round(total, _SCORE_DECIMALS)),
        (1, 0.0) if slack is None else (0, slack),
        (1, 0) if successors is None else (0, -successors),
        (1, 0.0) if not cd_known else (0, -score.consequence_of_delay),
        task_id,
    )


def _resource_warnings(
    conflict_analysis: ResourceConflictAnalysis | None,
) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """task_id -> (shared resource ids, names) from PROVEN conflicts only.

    ``UNKNOWN`` pairs are excluded deliberately: an overlap is not
    established there, so no warning is claimed. Per task, the shared
    resources are aggregated over ALL of its conflicting pairs (no
    duplicates); ids keep conflict-analysis order, names are sorted
    within each resource id for determinism.
    """
    if conflict_analysis is None:
        return {}
    aggregated: dict[str, dict[str, set[str]]] = {}
    for conflict in conflict_analysis.conflicts_with_status(ConflictStatus.CONFLICT):
        shared = list(zip(conflict.resource_ids, conflict.resource_names))
        for task_id in conflict.task_ids:
            per_task = aggregated.setdefault(task_id, {})
            for resource_id, resource_name in shared:
                per_task.setdefault(resource_id, set()).add(resource_name)
    warnings: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for task_id, per_task in aggregated.items():
        names: list[str] = []
        for group in per_task.values():
            names.extend(sorted(group))
        warnings[task_id] = (tuple(per_task), tuple(names))
    return warnings


def _ranked_task(
    *,
    rank: int,
    candidate_id: str,
    score: TaskPriorityScore | None,
    schedule: TaskSchedule | None,
    tasks_by_id: Mapping[str, object],
    warnings: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]],
) -> RankedTask:
    """Assemble one RankedTask; a missing score stays honestly None."""
    task = tasks_by_id.get(candidate_id)
    resource_ids, resource_names = warnings.get(candidate_id, ((), ()))
    return RankedTask(
        rank=rank,
        task_id=candidate_id,
        task_name=str(getattr(task, "name", candidate_id)),
        score=score,
        total_slack_days=schedule.total_slack_days if schedule is not None else None,
        successor_count=schedule.successor_count if schedule is not None else None,
        is_critical=schedule.is_critical if schedule is not None else None,
        resource_warning=bool(resource_ids),
        shared_resource_ids=tuple(resource_ids),
        shared_resource_names=tuple(resource_names),
    )
