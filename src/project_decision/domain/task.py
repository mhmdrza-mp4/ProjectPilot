"""Task concept: a piece of work with schedule, progress, and relationships.

Assumptions (documented, deliberately simple):
- ``duration_days`` is the planned duration in calendar working days.
- ``progress`` is expressed as a fraction in [0.0, 1.0].
- Dates are plain calendar dates (``datetime.date``); exact MS Project
  datetime semantics are handled later by the integration layer.
- Dependencies and assigned resources are referenced by id; a Task does
  not own Task or Resource objects. Referential integrity is checked at
  the Project level, where both sides are known.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Task:
    """A unit of work in the project."""

    id: str
    name: str
    duration_days: float
    progress: float = 0.0
    start: date | None = None
    finish: date | None = None
    dependency_ids: list[str] = field(default_factory=list)
    assigned_resource_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Task.id must not be empty")
        if not self.name:
            raise ValueError("Task.name must not be empty")
        if self.duration_days < 0:
            raise ValueError("Task.duration_days must be >= 0")
        if not 0.0 <= self.progress <= 1.0:
            raise ValueError("Task.progress must be between 0.0 and 1.0")
        if self.start is not None and self.finish is not None and self.finish < self.start:
            raise ValueError("Task.finish must not be before Task.start")
