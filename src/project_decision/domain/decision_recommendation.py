"""DecisionRecommendation concept: the selected decision plus its reasoning."""

from __future__ import annotations

from dataclasses import dataclass

from project_decision.domain.decision import Decision
from project_decision.domain.decision_evaluation import DecisionEvaluation


@dataclass
class DecisionRecommendation:
    """The decision the system recommends, and why."""

    decision: Decision
    evaluation: DecisionEvaluation
    reasoning: str

    def __post_init__(self) -> None:
        if self.decision is None:
            raise ValueError("DecisionRecommendation.decision must not be None")
        if self.evaluation is None:
            raise ValueError("DecisionRecommendation.evaluation must not be None")
        if not self.reasoning:
            raise ValueError("DecisionRecommendation.reasoning must not be empty")
        if self.evaluation.decision != self.decision:
            raise ValueError("recommendation decision and evaluation must refer to the same decision")
        if not self.evaluation.is_feasible:
            raise ValueError("only a feasible evaluation can be recommended")
