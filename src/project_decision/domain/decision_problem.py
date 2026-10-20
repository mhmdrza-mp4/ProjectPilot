"""DecisionProblem concept: the question the planner asks the system.

It bundles the current state, what the planner wants to achieve, the
constraints that must hold, and free-text context. No evaluation logic
lives here; a problem is only posed. Answering it is application-layer
work (the JEV integration, later).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from project_decision.domain.constraint import Constraint
from project_decision.domain.project_state import ProjectState


@dataclass
class DecisionProblem:
    """A request to evaluate decisions against a project state."""

    current_state: ProjectState
    objective: str                      # e.g. "reduce schedule delay"
    constraints: list[Constraint] = field(default_factory=list)
    context: str = ""                   # any planner-provided situation notes

    def __post_init__(self) -> None:
        if self.current_state is None:
            raise ValueError("DecisionProblem.current_state must not be None")
        if not self.objective:
            raise ValueError("DecisionProblem.objective must not be empty")
        _ids = {c.id for c in self.constraints}
        if len(_ids) != len(self.constraints):
            raise ValueError("DecisionProblem has duplicate constraint ids")
