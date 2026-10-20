"""Decision concept: one candidate action the planner could take.

A decision is described, not executed. The current vocabulary of
kinds covers the plain schedule-control moves a planner can make;
the actual effect computation will belong to the evaluation logic
(application layer + later JEV).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DecisionKind(Enum):
    """The type of project-control action a decision proposes."""

    ADD_RESOURCE_CAPACITY = "add_resource_capacity"
    ADJUST_TASK_DURATION = "adjust_task_duration"
    RESEQUENCE_TASKS = "resequence_tasks"
    LOWER_TASK_SCOPE = "lower_task_scope"
    OTHER = "other"


@dataclass
class Decision:
    """A possible project-control action."""

    id: str
    kind: DecisionKind
    description: str

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Decision.id must not be empty")
        if not self.description:
            raise ValueError("Decision.description must not be empty")
