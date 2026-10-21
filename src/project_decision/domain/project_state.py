"""ProjectState concept: a snapshot of a project at one point in time.

A state is a copy of what the planner saw in Microsoft Project when the
decision problem was posed. Decisions are evaluated against a state,
never against a live project object, so that evaluations stay
reproducible and later project edits do not silently change them.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING

from project_decision.domain.constraint import Constraint
from project_decision.domain.milestone import Milestone
from project_decision.domain.resource import Resource
from project_decision.domain.task import Task

if TYPE_CHECKING:
    from project_decision.domain.project import Project


def _snapshot[T](items: list[T]) -> list[T]:
    """Shallow-copy the list and each element, so snapshot and project diverge."""
    return [copy.copy(item) for item in copy.copy(items)]


@dataclass(frozen=True)
class ProjectState:
    """The relevant state of a project, captured at a specific time.

    Optional schedule/baseline fields mean exactly what the source
    reported at capture time: ``None`` = not available. Values are never
    invented (e.g. no baseline maps to ``None``, not to today's date).

    ``capture_warnings`` carries non-fatal diagnostics from the capture
    boundary (for example references to rows that could not be mapped and
    were therefore omitted). An empty tuple means the capture was
    complete; it is never a claim that the source contained no such
    references, only that none were dropped by the mapping.
    """

    project_id: str
    captured_at: datetime
    project_name: str = ""
    tasks: list[Task] = field(default_factory=list)
    resources: list[Resource] = field(default_factory=list)
    milestones: list[Milestone] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    project_start: date | None = None
    project_finish: date | None = None
    status_date: date | None = None
    baseline_start: date | None = None
    baseline_finish: date | None = None
    capture_warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValueError("ProjectState.project_id must not be empty")
        if self.captured_at is None:
            raise ValueError("ProjectState.captured_at must not be None")

    @classmethod
    def from_project(cls, project: Project, captured_at: datetime) -> ProjectState:
        """Snapshot a project. Copies content so later edits do not leak in."""
        return cls(
            project_id=project.id,
            captured_at=captured_at,
            project_name=project.name,
            tasks=_snapshot(project.tasks),
            resources=_snapshot(project.resources),
            milestones=_snapshot(project.milestones),
            constraints=_snapshot(project.constraints),
            project_start=project.project_start,
            project_finish=project.project_finish,
            status_date=project.status_date,
            baseline_start=project.baseline_start,
            baseline_finish=project.baseline_finish,
        )
