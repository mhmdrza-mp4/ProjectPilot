"""Project concept: the aggregate of tasks, resources, milestones, constraints.

The Project is also where referential integrity between objects is
checked, because only it sees all of them (tasks reference task ids and
resource ids that must exist).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from project_decision.domain.constraint import Constraint
from project_decision.domain.milestone import Milestone
from project_decision.domain.project_state import ProjectState
from project_decision.domain.resource import Resource
from project_decision.domain.task import Task


def _require_unique_ids(items: list[Task] | list[Resource] | list[Milestone] | list[Constraint], what: str) -> None:
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            raise ValueError(f"duplicate {what} id: {item.id}")
        seen.add(item.id)


@dataclass
class Project:
    """The planned and current content of a project."""

    id: str
    name: str
    tasks: list[Task] = field(default_factory=list)
    resources: list[Resource] = field(default_factory=list)
    milestones: list[Milestone] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Project.id must not be empty")
        if not self.name:
            raise ValueError("Project.name must not be empty")
        _require_unique_ids(self.tasks, "task")
        _require_unique_ids(self.resources, "resource")
        _require_unique_ids(self.milestones, "milestone")
        _require_unique_ids(self.constraints, "constraint")
        self._check_references()

    def _check_references(self) -> None:
        task_ids = {task.id for task in self.tasks}
        resource_ids = {resource.id for resource in self.resources}

        for task in self.tasks:
            for dependency_id in task.dependency_ids:
                if dependency_id == task.id:
                    raise ValueError(f"task {task.id} depends on itself")
                if dependency_id not in task_ids:
                    raise ValueError(f"task {task.id} depends on unknown task {dependency_id}")
            for resource_id in task.assigned_resource_ids:
                if resource_id not in resource_ids:
                    raise ValueError(f"task {task.id} references unknown resource {resource_id}")

    def task(self, task_id: str) -> Task:
        """Return the task with the given id, or raise KeyError."""
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(f"unknown task id: {task_id}")

    def capture_state(self, captured_at: datetime) -> ProjectState:
        """Snapshot this project at a point in time."""
        return ProjectState.from_project(self, captured_at)
