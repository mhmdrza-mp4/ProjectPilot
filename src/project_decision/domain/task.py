"""Task concept: a piece of work with schedule, progress, and relationships.

Assumptions (documented, deliberately simple):
- ``duration_days`` is the planned duration in calendar working days.
- ``progress`` is expressed as a fraction in [0.0, 1.0].
- Dates are plain calendar dates (``datetime.date``); exact MS Project
  datetime semantics are handled later by the integration layer.
- Dependencies and assigned resources are referenced by id; a Task does
  not own Task or Resource objects. Referential integrity is checked at
  the Project level, where both sides are known.
- ``total_slack_days`` is Microsoft Project's TotalSlack expressed in
  the same working-day unit as ``duration_days`` (COM reports minutes).
  It MAY be negative for tasks that are already late; ``None`` means
  the source did not report it.
- ``is_critical`` mirrors MSP's Critical flag (True = on the critical
  path); ``None`` means not reported — no criticality is ever derived
  here from other fields.
- ``notes`` is the MSP task Notes text, or ``None`` when the source has
  none. Empty/whitespace-only notes are treated as "none" by the mapper.
- ``is_summary`` mirrors MSP's Summary flag (a row that only groups its
  subtasks and carries no work itself). The MSP adapter currently skips
  summary rows entirely; the flag exists so snapshots and any future
  mappable summary rows stay distinguishable from normal tasks. It is
  NEVER inferred from names, indentation, duration, or dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Task:
    """A unit of work in a project."""

    id: str
    name: str
    duration_days: float
    progress: float = 0.0
    start: date | None = None
    finish: date | None = None
    dependency_ids: list[str] = field(default_factory=list)
    assigned_resource_ids: list[str] = field(default_factory=list)
    total_slack_days: float | None = None
    is_critical: bool | None = None
    notes: str | None = None
    is_summary: bool = False

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
