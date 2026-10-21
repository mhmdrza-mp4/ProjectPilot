from datetime import datetime

import pytest

from project_decision.domain import (
    DecisionAssumption,
    DecisionProblem,
    EvaluationCriterion,
    ProjectState,
)
from tests.conftest import (
    CAPTURED_AT,
    make_constraint,
    make_decision,
    make_project,
    make_resource,
    make_task,
)


def make_state() -> ProjectState:
    project = make_project(
        task=make_task(progress=0.25),
        resource=make_resource("R1"),
        constraints=[make_constraint()],
    )
    return project.capture_state(captured_at=CAPTURED_AT)


def make_problem(**overrides) -> DecisionProblem:
    defaults = dict(
        current_state=make_state(),
        objective="reduce schedule delay",
        title="Schedule recovery",
        description="A potential schedule issue needs a control decision.",
    )
    defaults.update(overrides)
    return DecisionProblem(**defaults)


class TestProjectState:
    def test_valid_state(self):
        state = make_state()

        assert state.project_id == "P1"
        assert state.captured_at == CAPTURED_AT
        assert state.tasks[0].progress == 0.25

    def test_standalone_state_without_project(self):
        state = ProjectState(project_id="P", captured_at=datetime(2026, 1, 1))
        assert state.tasks == []

    def test_empty_project_id_rejected(self):
        with pytest.raises(ValueError, match="project_id"):
            ProjectState(project_id="", captured_at=CAPTURED_AT)

    def test_missing_capture_time_rejected(self):
        with pytest.raises(ValueError, match="captured_at"):
            ProjectState(project_id="P", captured_at=None)  # type: ignore[arg-type]


class TestDecisionProblemCreation:
    def test_valid_problem_with_state(self):
        problem = make_problem()

        assert problem.title == "Schedule recovery"
        assert problem.objective == "reduce schedule delay"
        assert problem.id  # stable identifier is generated
        assert problem.alternatives == []
        assert problem.criteria == []
        assert problem.constraints == []
        assert problem.assumptions == []
        assert problem.context == ""

    def test_problem_without_a_captured_state_is_allowed_at_authoring_time(self):
        problem = make_problem(current_state=None)
        assert problem.current_state is None

    def test_full_problem(self):
        problem = make_problem(
            alternatives=[make_decision("D1"), make_decision("D2")],
            criteria=[EvaluationCriterion(id="C1", name="Schedule impact", description="days gained")],
            constraints=[make_constraint()],
            assumptions=[DecisionAssumption(id="A1", description="overtime is possible")],
            context="critical delivery next month",
        )

        assert len(problem.alternatives) == 2
        assert len(problem.criteria) == 1
        assert problem.assumptions[0].description == "overtime is possible"

    def test_generated_ids_are_unique_per_problem(self):
        assert make_problem().id != make_problem().id

    def test_explicit_id_is_preserved(self):
        assert make_problem(id="problem-42").id == "problem-42"


class TestDecisionProblemValidation:
    def test_empty_title_rejected(self):
        with pytest.raises(ValueError, match="title"):
            make_problem(title="")

    def test_empty_description_rejected(self):
        with pytest.raises(ValueError, match="description"):
            make_problem(description="")

    def test_empty_objective_rejected(self):
        with pytest.raises(ValueError, match="objective"):
            make_problem(objective="")

    def test_empty_id_rejected(self):
        with pytest.raises(ValueError, match="id"):
            make_problem(id="")

    def test_duplicate_alternative_ids_rejected(self):
        decision = make_decision("D1")
        with pytest.raises(ValueError, match="duplicate alternative"):
            make_problem(alternatives=[decision, make_decision("D1")])

    def test_duplicate_criterion_ids_rejected(self):
        criterion = EvaluationCriterion(id="C1", name="Risk", description="risk level")
        with pytest.raises(ValueError, match="duplicate criterion"):
            make_problem(criteria=[criterion, EvaluationCriterion(id="C1", name="Cost", description="cost")])

    def test_duplicate_assumption_ids_rejected(self):
        assumption = DecisionAssumption(id="A1", description="x")
        with pytest.raises(ValueError, match="duplicate assumption"):
            make_problem(assumptions=[assumption, DecisionAssumption(id="A1", description="y")])

    def test_duplicate_constraint_ids_rejected(self):
        constraint = make_constraint("C1")
        with pytest.raises(ValueError, match="duplicate constraint"):
            make_problem(constraints=[constraint, make_constraint("C1")])


class TestEvaluationReadiness:
    def test_authoring_problem_is_not_ready_without_alternatives(self):
        problem = make_problem()
        assert list(problem.evaluation_gaps()) == [
            "at least two alternatives are required for evaluation",
            "at least one evaluation criterion is required",
        ]
        assert problem.is_ready_for_evaluation() is False

    def test_one_alternative_is_still_not_ready(self):
        problem = make_problem(
            alternatives=[make_decision("D1")],
            criteria=[EvaluationCriterion(id="C1", name="Impact", description="schedule impact")],
        )
        assert problem.is_ready_for_evaluation() is False
        assert problem.evaluation_gaps() == [
            "at least two alternatives are required for evaluation"
        ]

    def test_ready_with_two_alternatives_and_one_criterion(self):
        problem = make_problem(
            alternatives=[make_decision("D1"), make_decision("D2")],
            criteria=[EvaluationCriterion(id="C1", name="Impact", description="schedule impact")],
        )
        assert problem.evaluation_gaps() == []
        assert problem.is_ready_for_evaluation() is True

    def test_two_alternatives_with_two_criteria(self):
        problem = make_problem(
            alternatives=[make_decision("D1"), make_decision("D2")],
            criteria=[
                EvaluationCriterion(id="C1", name="Impact", description="schedule"),
                EvaluationCriterion(id="C2", name="Risk", description="risk"),
            ],
        )
        assert problem.is_ready_for_evaluation() is True

    def test_criteria_require_descriptions(self):
        with pytest.raises(ValueError, match="description"):
            EvaluationCriterion(id="C1", name="Impact", description="")
