"""DecisionProblem concept: the question the planner asks the system.

A decision problem bundles:
- the objective and a title/description the planner gives it,
- the alternative actions under consideration (``Decision`` objects),
- the evaluation criteria the alternatives will be judged on
  (``EvaluationCriterion`` objects),
- known constraints (``Constraint`` — the same boundary model the
  project itself uses; see decision constraints below) and
- assumptions the planner is making (``DecisionAssumption``).
- optionally the captured ``ProjectState`` it was created from.

It is a *posed question* only: no evaluation, ranking, or effect
estimation happens here. Answering it is later application work (the
JEV integration). Consequently a problem may be authored while lacking
alternatives/criteria; ``evaluation_gaps()`` reports what is still
missing (at least two alternatives and at least one criterion are
required before evaluation).

Design notes:
- Decision constraints reuse ``Constraint`` (id + kind + description)
  instead of a duplicate class: a decision constraint is the same
  boundary concept, just authored by the planner rather than read from
  Microsoft Project.
- ``current_state`` is optional: a planner may describe a problem before
  a snapshot is available. Evaluating the problem later will require one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from project_decision.domain.constraint import Constraint
from project_decision.domain.decision import Decision
from project_decision.domain.decision_assumption import DecisionAssumption
from project_decision.domain.evaluation_criterion import EvaluationCriterion
from project_decision.domain.project_state import ProjectState


def _new_problem_id() -> str:
    return uuid4().hex


def _require_unique_ids(items: list[Any], what: str) -> None:
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"DecisionProblem has duplicate {what} ids")


@dataclass
class DecisionProblem:
    """A request to evaluate decision alternatives against a project state."""

    current_state: ProjectState | None
    objective: str
    title: str
    description: str
    id: str = field(default_factory=_new_problem_id)
    alternatives: list[Decision] = field(default_factory=list)
    criteria: list[EvaluationCriterion] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    assumptions: list[DecisionAssumption] = field(default_factory=list)
    context: str = ""  # optional free-text situation notes, beyond description

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("DecisionProblem.id must not be empty")
        if not self.title:
            raise ValueError("DecisionProblem.title must not be empty")
        if not self.description:
            raise ValueError("DecisionProblem.description must not be empty")
        if not self.objective:
            raise ValueError("DecisionProblem.objective must not be empty")
        _require_unique_ids(self.alternatives, "alternative")
        _require_unique_ids(self.criteria, "criterion")
        _require_unique_ids(self.assumptions, "assumption")
        _require_unique_ids(self.constraints, "constraint")

    # -- authoring helpers ----------------------------------------------------

    def evaluation_gaps(self) -> list[str]:
        """What is still missing before the problem can be evaluated."""
        gaps: list[str] = []
        if len(self.alternatives) < 2:
            gaps.append("at least two alternatives are required for evaluation")
        if not self.criteria:
            gaps.append("at least one evaluation criterion is required")
        return gaps

    def is_ready_for_evaluation(self) -> bool:
        """True when `evaluation_gaps` is empty."""
        return not self.evaluation_gaps()
