"""Tests for the decision review/validation layer (fully offline)."""

from copy import deepcopy
from datetime import date, datetime

import pytest

from project_decision.application.consequence_evaluation import (
    STATUS_FAILED,
    ConsequenceEvaluation,
    evaluate_session_consequences,
    score_session_with_evaluations,
)
from project_decision.application.decision_review import (
    ReviewStatus,
    review_decision_result,
    validate_decision_result,
)
from project_decision.application.jev_service import JevCallResult
from project_decision.application.priority_ranking import RankedTask, create_decision_result
from project_decision.application.schedule_analysis import analyze_schedule
from project_decision.domain import ProjectState, Resource
from project_decision.domain.planning_period import PlanningPeriod
from project_decision.infrastructure.jev.models import JevScoreAnswer
from tests.conftest import make_task

CAPTURED_AT = datetime(2026, 10, 21, 9, 0)
WEEK = PlanningPeriod(start_date=date(2026, 6, 1), end_date=date(2026, 6, 5))
OTHER_WEEK = PlanningPeriod(start_date=date(2026, 7, 1), end_date=date(2026, 7, 5))

LOW_CONFIDENCE_REASON = (
    "JEV confidence is low (0.35 < 0.5); manager review recommended. "
    "The Jev value is still used in the score."
)


def make_state(tasks, resources=()) -> ProjectState:
    return ProjectState(
        project_id="P1",
        captured_at=CAPTURED_AT,
        project_name="Demo",
        tasks=list(tasks),
        resources=list(resources),
    )


def two_task_state() -> ProjectState:
    return make_state(
        [
            make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      notes="one", total_slack_days=0.0),
            make_task("B", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      notes="two", total_slack_days=0.0),
        ]
    )


def scored(task_id, *, total=80.0, cd=0.0, source="evaluated", needs_review=False,
           reasons=(), sp=0.5, si=0.5, names=None):
    from project_decision.application.priority_scoring import TaskPriorityScore

    return TaskPriorityScore(
        task_id=task_id,
        schedule_pressure=None if total is None else sp,
        successor_impact=None if total is None else si,
        consequence_of_delay=cd,
        consequence_source=source,
        schedule_contribution=None,
        successor_contribution=None,
        consequence_contribution=None,
        total_score=total,
        needs_review=needs_review,
        review_reasons=tuple(reasons),
    )


def ranked(task_id, score=None, *, rank=1, name=None, resource_warning=False):
    return RankedTask(
        rank=rank,
        task_id=task_id,
        task_name=name if name is not None else f"Task {task_id}",
        score=score,
        total_slack_days=None if score is None else 0.0,
        successor_count=None if score is None else 0,
        is_critical=None,
        resource_warning=resource_warning,
    )


def broken_result(bad_ranked, *, period=WEEK) -> object:
    from project_decision.application.priority_ranking import DecisionResult

    return DecisionResult(planning_period=period, ranked_tasks=tuple(bad_ranked))


class TestReadyStatus:
    def test_valid_result_with_no_review_flags_is_ready(self):
        from project_decision.application.planning_session import create_planning_session
        from project_decision.application.priority_scoring import score_planning_session

        state = two_task_state()
        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)
        scores = score_planning_session(
            session, analysis, consequence_of_delay={"A": 0.0, "B": 0.0}
        )
        result = create_decision_result(session, state, analysis, scores)

        review = review_decision_result(
            session, state, result,
            schedule_analysis=analysis,
            consequence_evaluations=None,
        )

        assert review.status is ReviewStatus.READY
        assert review.validation_issues == ()
        assert review.tasks_needing_review == ()
        assert review.review_reason_counts == {}
        assert review.resource_warning_count == 0
        assert review.missing_schedule_count == 0
        assert review.provisional_consequence_count == 0
        assert review.low_confidence_consequence_count == 0
        assert review.unscored_task_ids == ()
        assert review.candidate_count == 2
        assert review.ranked_count == 2


class TestReviewRequired:
    def test_provisional_consequence_requires_review(self):
        from project_decision.application.planning_session import create_planning_session
        from project_decision.application.priority_scoring import score_planning_session

        state = two_task_state()
        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)
        scores = score_planning_session(session, analysis)  # no CD mapping → provisional
        result = create_decision_result(session, state, analysis, scores)

        review = review_decision_result(session, state, result)

        assert review.status is ReviewStatus.REVIEW_REQUIRED
        assert review.provisional_consequence_count == 2
        assert review.tasks_needing_review == ("A", "B")
        assert review.review_reason_counts == {"provisional_consequence": 2}

    def test_low_jev_confidence_requires_review(self):
        from project_decision.application.planning_session import create_planning_session
        from project_decision.application.priority_scoring import score_planning_session

        state = two_task_state()
        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)
        scores = score_planning_session(
            session, analysis, consequence_of_delay={"A": 2 / 3}
        )  # A evaluated, but the evaluation flagged low confidence
        result = create_decision_result(session, state, analysis, scores)
        evaluations = {
            "A": ConsequenceEvaluation(
                task_id="A",
                status="evaluated",
                consequence_of_delay=2 / 3,
                confidence=0.35,
                review_reasons=(LOW_CONFIDENCE_REASON,),
            )
        }

        review = review_decision_result(
            session, state, result, consequence_evaluations=evaluations
        )

        assert review.status is ReviewStatus.REVIEW_REQUIRED
        assert review.low_confidence_consequence_count == 1
        assert review.provisional_consequence_count == 1  # B still provisional
        assert review.tasks_needing_review == ("A", "B")
        assert review.review_reason_counts == {
            "provisional_consequence": 1,
            "low_consequence_confidence": 1,
        }

    def test_failed_and_unavailable_evaluations_require_review(self):
        from project_decision.application.planning_session import create_planning_session
        from project_decision.application.priority_scoring import score_planning_session

        state = two_task_state()
        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)
        scores = score_planning_session(session, analysis)
        result = create_decision_result(session, state, analysis, scores)
        evaluations = {
            "A": ConsequenceEvaluation(
                task_id="A", status=STATUS_FAILED, review_reasons=("JEV evaluation failed: api down",)
            ),
            "B": ConsequenceEvaluation(
                task_id="B", status="unavailable",
                review_reasons=("JEV evaluation unavailable: task notes are missing or insufficient.",),
            ),
        }

        review = review_decision_result(
            session, state, result, consequence_evaluations=evaluations
        )

        assert review.status is ReviewStatus.REVIEW_REQUIRED
        assert review.review_reason_counts == {
            "provisional_consequence": 2,
            "consequence_evaluation_failed": 1,
            "consequence_evaluation_unavailable": 1,
        }

    def test_missing_schedule_data_requires_review(self):
        from project_decision.application.planning_session import create_planning_session
        from project_decision.application.priority_scoring import score_planning_session

        # Task B has NO slack value → SP unavailable → no total score,
        # and the review reports exactly that (nothing invented).
        state = make_state(
            [
                make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                          total_slack_days=0.0),
                make_task("B", start=date(2026, 6, 1), finish=date(2026, 6, 2)),
            ]
        )
        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)
        scores = score_planning_session(
            session, analysis, consequence_of_delay={"A": 0.0, "B": 0.0}
        )
        result = create_decision_result(session, state, analysis, scores)

        review = review_decision_result(
            session, state, result, schedule_analysis=analysis
        )

        assert review.status is ReviewStatus.REVIEW_REQUIRED
        assert review.missing_schedule_count == 1
        assert review.tasks_with_missing_schedule == ("B",)
        assert review.tasks_needing_review == ("B",)
        assert review.review_reason_counts == {"missing_schedule_data": 1}

    def test_resource_warning_requires_review_but_nothing_else(self):
        from project_decision.application.resource_conflicts import analyze_resource_conflicts

        state = make_state(
            [
                make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                          total_slack_days=0.0, assigned_resource_ids=("R1",)),
                make_task("B", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                          total_slack_days=0.0, assigned_resource_ids=("R1",)),
            ],
            resources=[Resource(id="R1", name="Alice")],
        )
        session, analysis, scores = self._scored_pipeline(state)
        conflicts = analyze_resource_conflicts(state)
        result = create_decision_result(session, state, analysis, scores, conflicts)

        review = review_decision_result(session, state, result, schedule_analysis=analysis)

        assert review.status is ReviewStatus.REVIEW_REQUIRED  # warnings, not INVALID
        assert review.resource_warning_count == 2
        assert review.tasks_with_resource_warnings == ("A", "B")
        assert review.review_reason_counts == {"resource_conflict": 2}
        assert review.provisional_consequence_count == 0

    def _scored_pipeline(self, state):
        from project_decision.application.planning_session import create_planning_session
        from project_decision.application.priority_scoring import score_planning_session

        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)
        scores = score_planning_session(
            session, analysis, consequence_of_delay={"A": 0.0, "B": 0.0}
        )
        return session, analysis, scores

    def test_unscored_candidate_requires_review_without_fabricating(self):
        from project_decision.application.planning_session import create_planning_session

        state = two_task_state()
        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)
        result = create_decision_result(
            session, state, analysis, [scored("A")]
        )  # B never scored

        review = review_decision_result(session, state, result, schedule_analysis=analysis)

        assert review.status is ReviewStatus.REVIEW_REQUIRED
        assert review.unscored_task_ids == ("B",)
        assert review.unscored_count == 1
        assert review.review_reason_counts == {"unscored_candidate": 1}
        assert review.provisional_consequence_count == 0  # never invented


class TestValidationIssues:
    def _valid_result_parts(self):
        state = two_task_state()
        session, analysis, scores = self._scored_fresh(state)
        result = create_decision_result(session, state, analysis, scores)
        assert validate_decision_result(session, state, result) == ()
        return session, state, result

    def _scored_fresh(self, state):
        from project_decision.application.planning_session import create_planning_session
        from project_decision.application.priority_scoring import score_planning_session

        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)
        scores = score_planning_session(
            session, analysis, consequence_of_delay={"A": 0.0, "B": 0.0}
        )
        return session, analysis, scores

    def test_duplicate_task_ids_are_invalid(self):
        session, state, _ = self._valid_result_parts()

        bad = broken_result(
            [
                ranked("A", scored("A")),
                ranked("B", scored("B"), rank=2),
                ranked("A", scored("A"), rank=3),
            ]
        )

        issues = validate_decision_result(session, state, bad)

        assert any("appears 2 times" in issue for issue in issues)

    def test_missing_candidate_is_invalid(self):
        session, state, _ = self._valid_result_parts()

        bad = broken_result([ranked("A", scored("A"))])

        assert any("candidate 'B' is missing" in issue for issue in
                   validate_decision_result(session, state, bad))

    def test_extra_task_outside_the_session_is_invalid(self):
        session, state, _ = self._valid_result_parts()
        wider_state = make_state(
            [*state.tasks,
             make_task("Z", start=date(2026, 6, 1), finish=date(2026, 6, 2))]
        )

        bad = broken_result(
            [ranked("A", scored("A")), ranked("Z", scored("Z"), rank=2, name="Task Z")]
        )

        issues = validate_decision_result(session, wider_state, bad)
        assert any("is not a session candidate" in issue for issue in issues)

    def test_non_sequential_ranks_are_invalid(self):
        session, state, _ = self._valid_result_parts()

        bad = broken_result(
            [ranked("A", scored("A"), rank=2), ranked("B", scored("B"), rank=5)]
        )

        issues = validate_decision_result(session, state, bad)
        assert any("has rank 2; expected 1" in issue for issue in issues)
        assert any("has rank 5; expected 2" in issue for issue in issues)

    def test_candidate_count_mismatch_is_reported(self):
        session, state, _ = self._valid_result_parts()

        bad = broken_result([ranked("A", scored("A"))])  # 1 ranked vs 2 candidates

        issues = validate_decision_result(session, state, bad)
        assert any("candidate count mismatch" in issue for issue in issues)

    def test_period_mismatch_is_invalid(self):
        session, state, _ = self._valid_result_parts()

        bad = broken_result(
            [ranked("A", scored("A")), ranked("B", scored("B"), rank=2)],
            period=OTHER_WEEK,
        )

        issues = validate_decision_result(session, state, bad)
        assert any("planning period mismatch" in issue for issue in issues)

    def test_unknown_task_id_is_invalid(self):
        session, state, _ = self._valid_result_parts()

        bad = broken_result(
            [ranked("A", scored("A")), ranked("ZZZ", None, rank=2)]
        )

        issues = validate_decision_result(session, state, bad)
        assert any("not a session candidate" in issue for issue in issues)
        assert any("does not exist in the project state" in issue for issue in issues)

    def test_name_mismatch_is_invalid(self):
        session, state, _ = self._valid_result_parts()

        bad = broken_result(
            [ranked("A", scored("A"), name="Wrong Name"),
             ranked("B", scored("B"), rank=2)]
        )

        issues = validate_decision_result(session, state, bad)
        assert any("has name 'Wrong Name'" in issue for issue in issues)

    def test_off_candidate_in_rank_only_reported_not_scoped(self):
        # Review considers ONLY session candidates (scope invariant) but a
        # structural violation STILL fails validation (it is INVALID).
        session, state, _ = self._valid_result_parts()
        extra_state = make_state(
            [*state.tasks, make_task("Z", start=date(2026, 6, 1), finish=date(2026, 6, 2))]
        )
        bad = broken_result(
            [ranked("A", scored("A")), ranked("Z", scored("Z"), rank=2, name="Task Z")]
        )
        issues = validate_decision_result(session, extra_state, bad)
        assert issues  # INVALID

        review = review_decision_result(
            session, extra_state,
            broken_result([ranked("A", scored("A")), ranked("B", scored("B"), rank=2)]),
        )
        assert "Z" not in review.tasks_needing_review  # non-candidates never scoped


class TestStatusPrecedence:
    def test_invalid_beats_review_required(self):
        from project_decision.application.resource_conflicts import analyze_resource_conflicts

        state = make_state(
            [
                make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                          total_slack_days=0.0, assigned_resource_ids=("R1",)),
                make_task("B", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                          total_slack_days=0.0, assigned_resource_ids=("R1",)),
            ],
            resources=[Resource(id="R1", name="Alice")],
        )
        session, analysis, scores = self._scored_pipeline(state)
        conflicts = analyze_resource_conflicts(state)  # warnings + provisional scores
        result_with_flags = create_decision_result(session, state, analysis, scores, conflicts)
        assert review_decision_result(
            session, state, result_with_flags
        ).status is ReviewStatus.REVIEW_REQUIRED

        # Now break the structure on top: duplicate A with shifted ranks.
        bad = broken_result(
            [ranked("A", scores[0], resource_warning=True),
             ranked("A", scores[0], rank=2, resource_warning=True),
             ranked("B", scores[1], rank=3)]
        )

        review = review_decision_result(
            session, state, bad, schedule_analysis=analysis, consequence_evaluations=None
        )

        assert review.status is ReviewStatus.INVALID  # INVALID > REVIEW_REQUIRED
        assert review.validation_issues
        assert review.resource_warning_count == 2  # aggregation still reported

    def test_review_required_beats_ready(self):
        state = two_task_state()
        session, analysis, scores = self._scored_pipeline_provisional(state)
        result = create_decision_result(session, state, analysis, scores)

        review = review_decision_result(session, state, result)

        assert review.status is ReviewStatus.REVIEW_REQUIRED

    def _scored_pipeline_provisional(self, state):
        from project_decision.application.planning_session import create_planning_session
        from project_decision.application.priority_scoring import score_planning_session

        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)
        scores = score_planning_session(session, analysis)  # provisional for both
        return session, analysis, scores

    def _scored_pipeline(self, state):
        from project_decision.application.planning_session import create_planning_session
        from project_decision.application.priority_scoring import score_planning_session

        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)
        scores = score_planning_session(
            session, analysis, consequence_of_delay={"A": 0.0, "B": 0.0}
        )
        return session, analysis, scores


class TestDeterminismAndPurity:
    def test_repeated_runs_are_identical(self):
        state = two_task_state()
        session, analysis, scores = self._scored_pipeline_provisional(state)
        result = create_decision_result(session, state, analysis, scores)
        evaluations = (
            ConsequenceEvaluation(
                task_id="A",
                status=STATUS_FAILED,
                review_reasons=("JEV evaluation failed: api down",),
            ),
        )

        first = review_decision_result(
            session, state, result, schedule_analysis=analysis,
            consequence_evaluations=evaluations,
        )
        second = review_decision_result(
            session, state, result, schedule_analysis=analysis,
            consequence_evaluations=evaluations,
        )

        assert first == second

    def test_evaluation_input_order_is_irrelevant(self):
        state = two_task_state()
        session, analysis, scores = self._scored_pipeline_provisional(state)
        result = create_decision_result(session, state, analysis, scores)
        a = ConsequenceEvaluation(task_id="A", status=STATUS_FAILED,
                                  review_reasons=("JEV evaluation failed: x",))
        b = ConsequenceEvaluation(task_id="B", status="unavailable",
                                  review_reasons=("JEV evaluation unavailable: notes",))

        forward = review_decision_result(
            session, state, result, consequence_evaluations=(a, b)
        )
        reversed_run = review_decision_result(
            session, state, result, consequence_evaluations=(b, a)
        )

        assert forward == reversed_run

    def test_review_mutates_no_input(self):
        state = two_task_state()
        session, analysis, scores = self._scored_pipeline_provisional(state)
        result = create_decision_result(session, state, analysis, scores)
        evaluations = (
            ConsequenceEvaluation(
                task_id="A",
                status=STATUS_FAILED,
                review_reasons=("JEV evaluation failed: api down",),
            ),
        )
        pristine = (
            deepcopy(state), deepcopy(session), deepcopy(analysis),
            deepcopy(scores), deepcopy(result), deepcopy(evaluations),
        )

        review_decision_result(
            session, state, result, schedule_analysis=analysis,
            consequence_evaluations=evaluations,
        )

        assert (
            state, session, analysis, scores, result, evaluations
        ) == pristine

    def _scored_pipeline_provisional(self, state):
        from project_decision.application.planning_session import create_planning_session
        from project_decision.application.priority_scoring import score_planning_session

        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)
        scores = score_planning_session(session, analysis)
        return session, analysis, scores


class TestFullPipelineIntegration:
    def test_pipeline_through_jev_evaluations_scoring_ranking_review(self):
        from project_decision.application.planning_session import create_planning_session

        def jev_send(jev_state, questions, **kwargs):
            if "failing" in jev_state["task"]["notes"]:
                return JevCallResult(ok=False, error_message="api down", error_kind="api")
            return JevCallResult(
                ok=True,
                model="jev-latest",
                request_id="r1",
                answers={
                    "consequence_of_delay": JevScoreAnswer(
                        2.0, {0: "none", 1: "small", 2: "significant", 3: "major"},
                        {0: 0.1, 1: 0.2, 2: 0.6, 3: 0.1}, 0.9,
                    )
                },
            )

        state = make_state(
            [
                make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                          notes="ok", total_slack_days=0.0),
                make_task("B", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                          notes="failing push", total_slack_days=3.0),
            ]
        )
        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)
        evaluations = evaluate_session_consequences(session, state, send=jev_send)
        scores = score_session_with_evaluations(session, analysis, evaluations)
        result = create_decision_result(session, state, analysis, scores)

        review = review_decision_result(
            session, state, result,
            schedule_analysis=analysis,
            consequence_evaluations=evaluations,
        )

        assert review.status is ReviewStatus.REVIEW_REQUIRED
        assert review.validation_issues == ()
        assert review.candidate_count == 2
        assert review.ranked_count == 2
        # A evaluated at JEV level 2 → CD 2/3; B's JEV call failed → its
        # CD fell back to the provisional neutral (and review says why).
        assert review.provisional_consequence_count == 1
        assert review.review_reason_counts == {
            "provisional_consequence": 1,
            "consequence_evaluation_failed": 1,
        }
        assert review.tasks_needing_review == ("B",)
        # Ranking itself untouched: A ranks first (SP 1.0 vs 0.4 + CD edge).
        # Rank order itself untouched: A ranks first (SP 1.0 vs 0.4, plus
        # the evaluated CD edge over B's provisional fallback).
        first_ranked, second_ranked = result.ranked_tasks[0], result.ranked_tasks[1]
        assert first_ranked.task_id == "A" and second_ranked.task_id == "B"
