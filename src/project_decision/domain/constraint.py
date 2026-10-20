"""Constraint concept: something that limits which decisions are acceptable.

ConstraintKind is intentionally a small, generic vocabulary. It describes
why a constraint matters for decision-making; the detailed scheduling
semantics (e.g. MS Project dependency types, constraint types like
"Finish No Later Than") stay out of the domain and belong to the future
Microsoft Project integration layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ConstraintKind(Enum):
    """Why something restricts the choice of decisions."""

    DEADLINE = "deadline"                      # a date the project must respect
    RESOURCE_CAPACITY = "resource_capacity"    # a resource is only available up to a limit
    TASK_DEPENDENCY = "task_dependency"        # tasks must be executed in a given order
    MILESTONE = "milestone"                    # a milestone must be reached (on time)
    OTHER = "other"                            # project-specific constraint


@dataclass
class Constraint:
    """A single project constraint, described in domain terms."""

    id: str
    kind: ConstraintKind
    description: str

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Constraint.id must not be empty")
        if not self.description:
            raise ValueError("Constraint.description must not be empty")
