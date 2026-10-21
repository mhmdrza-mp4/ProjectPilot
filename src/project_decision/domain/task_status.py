"""Task status classification: objective rules based only on progress.

Rules (documented, deliberately simple — nothing else is assumed):
- COMPLETED   : progress >= 1.0 (100 %)
- IN_PROGRESS : 0.0 < progress < 1.0
- NOT_STARTED : progress == 0.0

A task with 0 % progress is NOT_STARTED, never "overdue": no comparison
with any date is made here. Whether a task is late requires baseline and
status-date data plus defined comparison rules, which belong to explicit
delay indicators (see application/project_analysis.py), not to task
classification.

Invalid data (progress outside 0..1) cannot reach this function:
:dataclass:`project_decision.domain.task.Task` validates it on creation.
"""

from __future__ import annotations

from enum import Enum

from project_decision.domain.task import Task


class TaskStatus(Enum):
    """Completion state of a task, derived only from its progress."""

    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
    NOT_STARTED = "not_started"


def classify_task(task: Task) -> TaskStatus:
    """Classify a task by its progress fraction."""
    if task.progress >= 1.0:
        return TaskStatus.COMPLETED
    if task.progress > 0.0:
        return TaskStatus.IN_PROGRESS
    return TaskStatus.NOT_STARTED


def task_status_label(status: TaskStatus) -> str:
    """Human-readable label for dashboard display."""
    return {
        TaskStatus.COMPLETED: "Completed",
        TaskStatus.IN_PROGRESS: "In progress",
        TaskStatus.NOT_STARTED: "Not started",
    }[status]
