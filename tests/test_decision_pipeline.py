"""Tests for the decision pipeline orchestration (fully offline)."""

import inspect
from copy import deepcopy
from datetime import date, datetime

import pytest

import project_decision.application.decision_pipeline as pipeline_module
from project_decision.application.consequence_evaluation import (
    STATUS_FAILED,
    ConsequenceEvaluation,
    evaluate_session_consequences,
)
from project_decision.application.decision_pipeline import (
    DecisionPipelineResult,
    run_decision_pipeline,
)
from project_decision.application.decision_review import ReviewStatus
from project_decision.application.jev_service import JevCallResult
from project_decision.application.priority_ranking import create_decision_result
from project_decision.application.decision_review import review_decision_result
from project_decision.application.priority_scoring import ScoringParameters
from project_decision.application.resource_conflicts import ConflictStatus
from project_decision.domain import ProjectState, Resource
from project_decision.domain.planning_period import PlanningPeriod
from project_decision.infrastructure.jev.models import JevScoreAnswer
from tests.conftest import make_task

CAPTURED_AT = datetime(2026, 10, 21, 9, 0)
WEEK = PlanningPeriod(start_date=date(2026, 6, 1), end_date=date(2026, 6, 5))


def make_state() -> ProjectState:
    return ProjectState(
        project_id="P1",
        captured_at=CAPTURED_AT,
        project_name="Demo",
        tasks=[
            make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      notes="permit needed", total_slack_days=0.0),
            make_task("B", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      notes="cleanup", total_slack_days=3.0,
                      assigned_resource_ids=("R1",)),
        ],
        resources=[Resource(id="R1", name="Alice")],
    )


def conflicting_state() -> ProjectState:
    """Both tasks share resource R1 over the same days → one CONFLICT pair."""
    return ProjectState(
        project_id="P1",
        captured_at=CAPTURED_AT,
        project_name="Demo",
        tasks=[
            make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      notes="permit needed", total_slack_days=0.0,
                      assigned_resource_ids=("R1",)),
            make_task("B", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      notes="cleanup", total_slack_days=3.0,
                      assigned_resource_ids=("R1",)),
        ],
        resources=[Resource(id="R1", name="Alice")],
    )


def session_of(state: ProjectState) -> object:
    from project_decision.application.planning_session import create_planning_session

    return create_planning_session(state, WEEK)


class TestCompletePipeline:
    def test_all_stages_are_composed_into_one_result(self):
        state = conflicting_state()
        session = session_of(state)

        def evaluator(session_arg, state_arg):
            return [
                ConsequenceEvaluation(
                    task_id="A", status="evaluated", consequence_of_delay=1.0
                ),
                ConsequenceEvaluation(task_id="B", status="unavailable"),
            ]

        result = run_decision_pipeline(state, session, evaluator)

        assert isinstance(result, DecisionPipelineResult)
        assert result.planning_session == session
        # Stage objects preserved verbatim (single execution inside).
        assert result.schedule_analysis.issues == ()
        assert set(result.schedule_analysis.schedules) == {"A", "B"}
        assert result.conflict_analysis.conflicts_with_status(
            ConflictStatus.CONFLICT
        )
        assert [evaluation.status for evaluation in result.consequence_evaluations
                if evaluation.task_id == "A"] == ["evaluated"]
        assert result.decision_result.planning_period == WEEK
        by_id = {r.task_id: r for r in result.decision_result.ranked_tasks}
        assert by_id["A"].consequence_of_delay == pytest.approx(1.0)
        assert by_id["A"].consequence_source == "evaluated"
        assert by_id["B"].consequence_source == "provisional"
        assert result.decision_review.status is ReviewStatus.REVIEW_REQUIRED


class TestExecutionOrder:
    def test_stages_run_once_in_the_documented_order(self, monkeypatch):
        calls: list[str] = []
        state = make_state()
        session = session_of(state)

        real_schedule = pipeline_module.analyze_schedule
        real_conflicts = pipeline_module.analyze_resource_conflicts
        real_scoring = pipeline_module.score_session_with_evaluations
        real_ranking = pipeline_module.create_decision_result
        real_review = pipeline_module.review_decision_result

        def spy_schedule(a_state):
            calls.append("schedule")
            return real_schedule(a_state)

        def spy_conflicts(a_state):
            calls.append("conflicts")
            return real_conflicts(a_state)

        def spy_evaluator(a_session, a_state):
            calls.append("consequence")
            return [
                ConsequenceEvaluation(
                    task_id="A", status="evaluated", consequence_of_delay=0.0
                ),
                ConsequenceEvaluation(
                    task_id="B", status="evaluated", consequence_of_delay=0.0
                ),
            ]

        def spy_scoring(a_session, analysis, evaluations, *, parameters=None):
            calls.append("scoring")
            return real_scoring(a_session, analysis, evaluations, parameters=parameters)

        def spy_ranking(*args, **kwargs):
            calls.append("ranking")
            return real_ranking(*args, **kwargs)

        def spy_review(*args, **kwargs):
            calls.append("review")
            return real_review(*args, **kwargs)

        monkeypatch.setattr(pipeline_module, "analyze_schedule", spy_schedule)
        monkeypatch.setattr(
            pipeline_module, "analyze_resource_conflicts", spy_conflicts
        )
        monkeypatch.setattr(
            pipeline_module, "score_session_with_evaluations", spy_scoring
        )
        monkeypatch.setattr(pipeline_module, "create_decision_result", spy_ranking)
        monkeypatch.setattr(pipeline_module, "review_decision_result", spy_review)

        result = run_decision_pipeline(state, session, spy_evaluator)

        assert calls == ["schedule", "conflicts", "consequence", "scoring",
                         "ranking", "review"]  # order AND exactly once each
        assert result.decision_review.status is ReviewStatus.READY


class TestDependencyInjection:
    def test_evaluator_is_called_and_its_output_reaches_every_stage(self):
        state = make_state()
        session = session_of(state)
        seen: list[tuple] = []

        def evaluator(e_session, e_state):
            seen.append((e_session, e_state))
            return [
                ConsequenceEvaluation(
                    task_id="A", status="evaluated", consequence_of_delay=1.0
                )
            ]

        result = run_decision_pipeline(state, session, evaluator)

        assert seen == [(session, state)]  # called once, with the exact inputs
        a = {r.task_id: r for r in result.decision_result.ranked_tasks}["A"]
        assert a.consequence_source == "evaluated"
        assert a.consequence_of_delay == pytest.approx(1.0)

    def test_the_pipeline_module_stays_jev_free(self):
        source = inspect.getsource(pipeline_module)
        assert "import typesafe" not in source
        assert "from typesafe" not in source
        assert "from project_decision.infrastructure" not in source
        assert "import streamlit" not in source
        assert "import fastapi" not in source
        assert "send_jev_questions" not in source


class TestScoringParameterPropagation:
    def test_explicit_parameters_are_passed_through_unchanged(self):
        from project_decision.application.consequence_evaluation import (
            score_session_with_evaluations,
        )

        state = make_state()
        session = session_of(state)
        parameters = ScoringParameters(
            schedule_pressure_weight=0.6,
            successor_impact_weight=0.1,
            consequence_of_delay_weight=0.3,
            successor_cap=2,
        )

        result = run_decision_pipeline(
            state, session, lambda s, _state: [], scoring_parameters=parameters
        )

        expected = score_session_with_evaluations(
            session, result.schedule_analysis, [], parameters=parameters
        )
        assert {
            ranked.score.task_id: ranked.score
            for ranked in result.decision_result.ranked_tasks
        } == {score.task_id: score for score in expected}

    def test_default_parameters_behave_unchanged(self):
        from project_decision.application.consequence_evaluation import (
            score_session_with_evaluations,
        )

        state = make_state()
        session = session_of(state)

        result = run_decision_pipeline(state, session, lambda s, _state: [])
        expected = score_session_with_evaluations(
            session, result.schedule_analysis, [], parameters=None
        )

        assert {
            ranked.score.task_id: ranked.score
            for ranked in result.decision_result.ranked_tasks
        } == {score.task_id: score for score in expected}


class TestReviewPropagation:
    def test_failed_evaluation_and_low_confidence_reach_the_review(self):
        state = make_state()
        session = session_of(state)
        evaluations = [
            ConsequenceEvaluation(
                task_id="A",
                status="evaluated",
                consequence_of_delay=2 / 3,
                confidence=0.35,
                review_reasons=(
                    "JEV confidence is low (0.35 < 0.5); manager review recommended. "
                    "The Jev value is still used in the score.",
                ),
            ),
            ConsequenceEvaluation(
                task_id="B",
                status=STATUS_FAILED,
                review_reasons=("JEV evaluation failed: api down",),
            ),
        ]

        result = run_decision_pipeline(state, session, lambda s, _t: evaluations)

        review = result.decision_review
        assert review.status is ReviewStatus.REVIEW_REQUIRED
        assert review.low_confidence_consequence_count == 1
        assert review.review_reason_counts == {
            "provisional_consequence": 1,
            "low_consequence_confidence": 1,
            "consequence_evaluation_failed": 1,
        }
        assert review.tasks_needing_review == ("A", "B")

    def test_missing_schedule_reaches_the_review(self):
        state = ProjectState(
            project_id="P1",
            captured_at=CAPTURED_AT,
            project_name="Demo",
            tasks=[
                make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                          total_slack_days=0.0),
                make_task("B", start=date(2026, 6, 1), finish=date(2026, 6, 2)),
            ],
        )
        session = session_of(state)

        result = run_decision_pipeline(state, session, lambda s, _t: [])

        review = result.decision_review
        assert review.status is ReviewStatus.REVIEW_REQUIRED
        assert review.missing_schedule_count == 1
        assert review.tasks_with_missing_schedule == ("B",)


class TestDeterminismAndPurity:
    def test_same_inputs_produce_equal_results(self):
        state = make_state()
        first_state = deepcopy(state)

        def evaluator(session_arg, state_arg):
            return [
                ConsequenceEvaluation(
                    task_id="A", status="evaluated", consequence_of_delay=1.0
                ),
                ConsequenceEvaluation(task_id="B", status="unavailable"),
            ]

        result = run_decision_pipeline(state, session_of(state), evaluator)
        assert state == first_state  # determinism inputs intact

        second = run_decision_pipeline(
            deepcopy(state), session_of(state), evaluator
        )

        assert result == second

    def test_pipeline_mutates_no_input(self):
        state = make_state()
        session = session_of(state)
        evaluations = [
            ConsequenceEvaluation(task_id="B", status="unavailable"),
        ]
        pristine_state = deepcopy(state)
        pristine_session = deepcopy(session)
        pristine_evals = deepcopy(evaluations)

        run_decision_pipeline(state, session, lambda s, _t: deepcopy(evaluations))

        assert state == pristine_state
        assert session == pristine_session
        assert evaluations == pristine_evals


class TestErrorBehavior:
    def test_unexpected_programming_errors_are_not_swallowed(self):
        state = make_state()
        session = session_of(state)

        def broken_evaluator(_session, _state):
            raise RuntimeError("programming bug")

        with pytest.raises(RuntimeError, match="programming bug"):
            run_decision_pipeline(state, session, broken_evaluator)

    def test_expected_evaluation_failure_stays_structured(self):
        state = make_state()
        session = session_of(state)

        def failing_for_all(session_arg, state_arg):
            return [
                ConsequenceEvaluation(
                    task_id="A",
                    status=STATUS_FAILED,
                    review_reasons=("JEV evaluation failed: api down",),
                ),
                ConsequenceEvaluation(
                    task_id="B",
                    status=STATUS_FAILED,
                    review_reasons=("JEV evaluation failed: api down",),
                ),
            ]

        result = run_decision_pipeline(state, session, failing_for_all)

        assert result.decision_review.status is ReviewStatus.REVIEW_REQUIRED
        assert result.decision_review.review_reason_counts == {
            "provisional_consequence": 2,
            "consequence_evaluation_failed": 2,
        }
        # Scores still exist (provisional CD), nothing fabricated.
        assert all(
            ranked.total_score is not None
            for ranked in result.decision_result.ranked_tasks
        )


class TestCandidateScopeAndEmptySession:
    def test_only_session_candidates_are_scored_and_ranked(self):
        state = ProjectState(
            project_id="P1",
            captured_at=CAPTURED_AT,
            project_name="Demo",
            tasks=[
                make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                          total_slack_days=0.0),
                make_task("OUT", start=date(2026, 1, 1), finish=date(2026, 1, 2),
                          total_slack_days=0.0),
            ],
        )
        session = session_of(state)  # default candidates: A only

        result = run_decision_pipeline(state, session, lambda s, _t: [])

        assert [r.task_id for r in result.decision_result.ranked_tasks] == ["A"]
        assert result.decision_review.candidate_count == 1

    def test_empty_candidate_session_yields_empty_decision_result(self):
        from project_decision.application.planning_session import create_planning_session

        state = make_state()
        session = create_planning_session(state, WEEK, candidate_task_ids=())

        result = run_decision_pipeline(state, session, lambda s, _t: [])

        assert result.decision_result.ranked_tasks == ()
        assert result.decision_result.candidate_count == 0
        assert result.decision_review.status is ReviewStatus.READY  # valid, no flags
        assert result.decision_review.ranked_count == 0
        assert result.decision_review.validation_issues == ()
        assert result.decision_review.tasks_needing_review == ()
        # The structural stages still ran (nothing fabricated).
        assert set(result.schedule_analysis.schedules) == {"A", "B"}


class TestFullPipelineIntegration:
    def test_end_to_end_offline_with_real_consequence_evaluation(self):
        state = conflicting_state()
        session = session_of(state)

        def jev_send(jev_state, questions, **kwargs):
            assert set(jev_state) == {"task"}  # name+notes only, no schedule data
            return JevCallResult(
                ok=True,
                model="jev-latest",
                request_id="r1",
                answers={
                    "consequence_of_delay": JevScoreAnswer(
                        3.0,
                        {0: "none", 1: "small", 2: "significant", 3: "major"},
                        {0: 0.02, 1: 0.08, 2: 0.2, 3: 0.7},
                        0.9,
                    )
                },
            )

        def evaluator(session_arg, state_arg):
            return evaluate_session_consequences(
                session_arg, state_arg, send=jev_send
            )

        result = run_decision_pipeline(state, session, evaluator)

        # -- stage completeness
        assert result.decision_review.validation_issues == ()
        assert result.decision_review.candidate_count == 2
        # -- resource warnings reached result AND review
        assert len(result.conflict_analysis.conflicts_with_status(
            ConflictStatus.CONFLICT
        )) == 1
        warnings = result.decision_result.tasks_with_resource_warnings()
        assert [w.task_id for w in warnings] == ["A", "B"]  # rank order
        assert result.decision_review.tasks_with_resource_warnings == ("A", "B")
        # -- consequence values: both evaluated at level 3 → CD 1.0
        by_id = {r.task_id: r for r in result.decision_result.ranked_tasks}
        assert by_id["A"].consequence_of_delay == pytest.approx(1.0)
        assert by_id["B"].consequence_of_delay == pytest.approx(1.0)
        assert by_id["A"].consequence_source == "evaluated"
        # -- exact expected ranking: A (SP 1.0, slack 0) before B (SP 0.4)
        assert [r.task_id for r in result.decision_result.ranked_tasks] == ["A", "B"]
        assert by_id["A"].total_score == pytest.approx(
            100 * 0.4 * 1.0 + 100 * 0.2 * 0.0 + 100 * 0.4 * 1.0
        )  # 80.0
        assert by_id["B"].total_score == pytest.approx(
            100 * 0.4 * (1 - 3 / 5) + 100 * 0.2 * 0.0 + 100 * 0.4 * 1.0
        )  # 56.0
        # -- review: fully evaluated, HIGH confidence → only the
        #    resource-conflict warnings require review.
        assert result.decision_review.status is ReviewStatus.REVIEW_REQUIRED
        assert result.decision_review.provisional_consequence_count == 0
        assert result.decision_review.low_confidence_consequence_count == 0
        assert result.decision_review.review_reason_counts == {
            "resource_conflict": 2
        }


class TestRankingPropagation:
    def test_pipeline_ranking_equals_the_standalone_implementation(self):
        state = make_state()
        session = session_of(state)
        evaluations = [
            ConsequenceEvaluation(
                task_id="A", status="evaluated", consequence_of_delay=0.5
            ),
        ]

        result = run_decision_pipeline(state, session, lambda s, _t: evaluations)

        scores = [ranked.score for ranked in result.decision_result.ranked_tasks]
        standalone = create_decision_result(
            session, state, result.schedule_analysis, scores,
            result.conflict_analysis,
        )
        assert result.decision_result == standalone
        assert result.decision_review == review_decision_result(
            session, state, standalone, schedule_analysis=result.schedule_analysis,
            consequence_evaluations=evaluations,
        )
