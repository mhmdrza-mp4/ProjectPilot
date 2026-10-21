"""Project analysis: basic, objectively defined indicators for a state.

This is descriptive analysis only — no optimization, no recommendations,
no candidate decisions, no JEV calls, no prediction. It answers "what
does the current state look like?" as an input to later decision-making.

Indicator definitions (documented, deliberately simple):

- Task counts classify by ``progress`` only (see domain/task_status.py):
  COMPLETED >= 1.0, IN_PROGRESS 0..1 exclusive, NOT_STARTED == 0.0.
  No date comparison is involved, so no task is called "overdue" here.
- "tasks_with_missing_dates" counts tasks whose start AND finish are both
  unavailable in the captured state.
- "planned_start"/"planned_finish" are the project-level start/finish the
  source reported (None = not available).
- "overall_completion" is the duration-weighted mean task progress, defined
  only when the total duration of all tasks is greater than zero; with
  zero-duration-only tasks the value is None rather than invented.
- The schedule-delay indicator is AVAILABLE ONLY with baseline data: it
  compares the scheduled project finish with the baseline finish. A late
  current finish alone says nothing about delay (a baseline finishing
  later than today is not a delay), and comparing finish dates with
  today's date is deliberately NOT done.

Missing values are represented as None everywhere and surfaced as "not
available" by the dashboard — never fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from project_decision.domain.project_state import ProjectState
from project_decision.domain.task_status import TaskStatus, classify_task


@dataclass(frozen=True)
class ScheduleDelay:
    """Baseline-based schedule-variance indicator for the project finish.

    available=False means the required inputs are missing; ``explanation``
    then states exactly what is missing so the dashboard can say so.
    """

    available: bool
    delay_days: int | None = None
    explanation: str = ""


@dataclass(frozen=True)
class ProjectAnalysis:
    """Basic indicators computed from one ProjectState."""

    total_tasks: int
    completed_tasks: int
    in_progress_tasks: int
    not_started_tasks: int
    tasks_with_missing_dates: int

    planned_start: date | None
    planned_finish: date | None
    status_date: date | None
    baseline_start: date | None
    baseline_finish: date | None

    overall_completion: float | None  # 0.0..1.0, duration-weighted; None if not definable
    schedule_delay: ScheduleDelay


def analyze_project_state(state: ProjectState) -> ProjectAnalysis:
    """Compute basic indicators from a captured project state."""
    total = len(state.tasks)
    completed = 0
    in_progress = 0
    not_started = 0
    missing_dates = 0

    for task in state.tasks:
        status = classify_task(task)
        if status is TaskStatus.COMPLETED:
            completed += 1
        elif status is TaskStatus.IN_PROGRESS:
            in_progress += 1
        else:
            not_started += 1
        if task.start is None and task.finish is None:
            missing_dates += 1

    return ProjectAnalysis(
        total_tasks=total,
        completed_tasks=completed,
        in_progress_tasks=in_progress,
        not_started_tasks=not_started,
        tasks_with_missing_dates=missing_dates,
        planned_start=state.project_start,
        planned_finish=state.project_finish,
        status_date=state.status_date,
        baseline_start=state.baseline_start,
        baseline_finish=state.baseline_finish,
        overall_completion=_overall_completion(state.tasks),
        schedule_delay=_schedule_delay(state),
    )


def _overall_completion(tasks: list) -> float | None:
    """Duration-weighted mean progress; None when weights are all zero."""
    total_duration = sum(task.duration_days for task in tasks)
    if not tasks or total_duration <= 0:
        return None
    weighted_progress = sum(task.duration_days * task.progress for task in tasks)
    return weighted_progress / total_duration


def _schedule_delay(state: ProjectState) -> ScheduleDelay:
    """Baseline finish vs. scheduled finish, in days; 0 means on/ahead of baseline.

    Requires a recorded baseline. Without one the metric is honestly
    unavailable — the current finish date alone does not indicate delay.
    """
    if state.baseline_finish is None:
        return ScheduleDelay(
            available=False,
            explanation="no baseline is recorded for this project "
                        "(BaselineFinish unavailable), so schedule delay vs. "
                        "baseline cannot be calculated",
        )
    if state.project_finish is None:
        return ScheduleDelay(
            available=False,
            explanation="the scheduled project finish date is unavailable",
        )
    variance = (state.project_finish - state.baseline_finish).days
    delay_days = max(0, variance)
    if delay_days == 0:
        explanation = (
            "the scheduled finish is on or before the baseline finish "
            f"({state.baseline_finish.isoformat()})"
        )
    else:
        explanation = (
            f"the scheduled finish ({state.project_finish.isoformat()}) is "
            f"{delay_days} day(s) later than the baseline finish "
            f"({state.baseline_finish.isoformat()})"
        )
    return ScheduleDelay(available=True, delay_days=delay_days, explanation=explanation)
