"""DecisionAssumption concept: something the planner takes as given.

Assumptions record what a decision problem *presupposes* (e.g. "overtime
is possible for at most two weeks"). They are planner-authored context,
not computed facts, and are carried with the problem unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DecisionAssumption:
    """An assumption the planner is making while posing the problem."""

    id: str
    description: str

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("DecisionAssumption.id must not be empty")
        if not self.description:
            raise ValueError("DecisionAssumption.description must not be empty")
