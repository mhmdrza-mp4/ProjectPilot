"""ProjectState concept: a snapshot of a project at one point in time.

A state is a copy of what the planner saw in Microsoft Project when the
decision problem was posed. Decisions are evaluated against a state,
never against a live project object, so that evaluations stay
reproducible and later project edits do not silently change them.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime
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
    """The relevant state of a project, captured at a specific time."""

    project_id: str
    captured_at: datetime
    tasks: list[Task] = field(default_factory=list)
    resources: list[Resource] = field(default_factory=list)
    milestones: list[Milestone] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)

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
            tasks=_snapshot(project.tasks),
            resources=_snapshot(project.resources),
            milestones=_snapshot(project.milestones),
            constraints=_snapshot(project.constraints),
        )
