import pytest

from project_decision.domain import DecisionEvaluation, DecisionRecommendation
from tests.conftest import make_decision


class TestDecisionRecommendation:
    def test_valid_recommendation(self):
        decision = make_decision()
        evaluation = DecisionEvaluation(decision=decision, is_feasible=True)
        recommendation = DecisionRecommendation(
            decision=decision,
            evaluation=evaluation,
            reasoning="adds capacity on the critical task without violating any deadline",
        )

        assert recommendation.decision is decision
        assert recommendation.evaluation is evaluation

    def test_empty_reasoning_rejected(self):
        decision = make_decision()
        evaluation = DecisionEvaluation(decision=decision, is_feasible=True)
        with pytest.raises(ValueError, match="reasoning"):
            DecisionRecommendation(
                decision=decision, evaluation=evaluation, reasoning=""
            )

    def test_infeasible_evaluation_cannot_be_recommended(self):
        decision = make_decision()
        evaluation = DecisionEvaluation(
            decision=decision, is_feasible=False, violated_constraints=["C1"]
        )
        with pytest.raises(ValueError, match="feasible"):
            DecisionRecommendation(decision=decision, evaluation=evaluation, reasoning="why")

    def test_evaluation_of_other_decision_rejected(self):
        decision = make_decision("D1")
        other = make_decision("D2")
        evaluation = DecisionEvaluation(decision=other, is_feasible=True)
        with pytest.raises(ValueError, match="same decision"):
            DecisionRecommendation(decision=decision, evaluation=evaluation, reasoning="why")
