import pytest

from project_decision.domain import DecisionEvaluation
from tests.conftest import make_decision


class TestDecision:
    def test_valid_decision(self):
        from project_decision.domain import DecisionKind

        decision = make_decision()
        assert decision.kind == DecisionKind.ADD_RESOURCE_CAPACITY
        assert decision.name == "Alternative D1"

    def test_empty_id_rejected(self):
        with pytest.raises(ValueError, match="id"):
            make_decision(decision_id="")

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name"):
            make_decision(decision_id="D", name="")

    def test_empty_description_rejected(self):
        with pytest.raises(ValueError, match="description"):
            make_decision(decision_id="D", description="")


class TestDecisionEvaluation:
    def test_feasible_evaluation(self):
        decision = make_decision()
        evaluation = DecisionEvaluation(decision=decision, is_feasible=True)

        assert evaluation.is_feasible is True
        assert evaluation.violated_constraints == []
        assert evaluation.projected_finish_impact_days is None

    def test_infeasible_evaluation_with_reasons(self):
        evaluation = DecisionEvaluation(
            decision=make_decision(),
            is_feasible=False,
            violated_constraints=["C1", "C2"],
        )
        assert evaluation.violated_constraints == ["C1", "C2"]

    def test_infeasible_without_reasons_rejected(self):
        with pytest.raises(ValueError, match="violated"):
            DecisionEvaluation(decision=make_decision(), is_feasible=False)

    def test_feasible_with_violations_rejected(self):
        with pytest.raises(ValueError, match="feasible"):
            DecisionEvaluation(
                decision=make_decision(),
                is_feasible=True,
                violated_constraints=["C1"],
            )

    def test_missing_decision_rejected(self):
        with pytest.raises(ValueError, match="decision"):
            DecisionEvaluation(decision=None, is_feasible=True)  # type: ignore[arg-type]

    def test_negative_impact_means_earlier_finish(self):
        evaluation = DecisionEvaluation(
            decision=make_decision(),
            is_feasible=True,
            projected_finish_impact_days=-3.0,
        )
        assert evaluation.projected_finish_impact_days == -3.0
