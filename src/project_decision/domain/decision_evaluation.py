"""DecisionEvaluation concept: the outcome of evaluating one candidate decision.

Assumption: for now an evaluation records feasibility plus reasons, and
an optional projected finish impact in days. Numeric scoring will gain
real meaning once the JEV integration exists; until then
``projected_finish_impact_days`` stays optional and descriptive.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from project_decision.domain.decision import Decision


@dataclass
class DecisionEvaluation:
    """What evaluating a single candidate decision produced."""

    decision: Decision
    is_feasible: bool
    violated_constraints: list[str] = field(default_factory=list)  # constraint ids
    projected_finish_impact_days: float | None = None              # negative = earlier finish
    notes: str = ""

    def __post_init__(self) -> None:
        if self.decision is None:
            raise ValueError("DecisionEvaluation.decision must not be None")
        if not self.is_feasible and not self.violated_constraints:
            raise ValueError("an infeasible evaluation must name violated constraints")
        if self.is_feasible and self.violated_constraints:
            raise ValueError("a feasible evaluation cannot have violated constraints")
