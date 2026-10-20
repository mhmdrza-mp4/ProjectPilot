from datetime import datetime

import pytest

from project_decision.domain import DecisionProblem, ProjectState
from tests.conftest import CAPTURED_AT, make_constraint, make_project, make_resource, make_task


def make_state() -> ProjectState:
    project = make_project(
        task=make_task(progress=0.25),
        resource=make_resource("R1"),
        constraints=[make_constraint()],
    )
    return project.capture_state(captured_at=CAPTURED_AT)


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


class TestDecisionProblem:
    def test_valid_problem(self):
        problem = DecisionProblem(
            current_state=make_state(),
            objective="reduce schedule delay",
            constraints=[make_constraint()],
            context="critical delivery next month",
        )

        assert problem.objective == "reduce schedule delay"
        assert len(problem.constraints) == 1
        assert problem.context == "critical delivery next month"

    def test_context_is_optional(self):
        problem = DecisionProblem(current_state=make_state(), objective="reduce schedule delay")
        assert problem.context == ""

    def test_missing_state_rejected(self):
        with pytest.raises(ValueError, match="current_state"):
            DecisionProblem(current_state=None, objective="x")  # type: ignore[arg-type]

    def test_empty_objective_rejected(self):
        with pytest.raises(ValueError, match="objective"):
            DecisionProblem(current_state=make_state(), objective="")

    def test_duplicate_constraint_ids_rejected(self):
        constraint = make_constraint("C1")
        with pytest.raises(ValueError, match="duplicate constraint"):
            DecisionProblem(
                current_state=make_state(),
                objective="x",
                constraints=[constraint, make_constraint("C1")],
            )
