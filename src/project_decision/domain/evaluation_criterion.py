"""EvaluationCriterion concept: one yardstick an alternative is judged on.

A criterion describes *what is measured* (e.g. "expected schedule
impact"); it deliberately carries no weights, no scoring, and no data.
Scoring alternatives against criteria is evaluation work and will belong
to the JEV integration, once its contract is known.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EvaluationCriterion:
    """A named measure used to evaluate the alternatives of a problem."""

    id: str
    name: str
    description: str

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("EvaluationCriterion.id must not be empty")
        if not self.name:
            raise ValueError("EvaluationCriterion.name must not be empty")
        if not self.description:
            raise ValueError("EvaluationCriterion.description must not be empty")
