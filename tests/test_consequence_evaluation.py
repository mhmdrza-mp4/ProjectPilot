"""Tests for the Jev consequence-of-delay evaluation (fully offline)."""

from copy import deepcopy
from dataclasses import replace as dc_replace
from datetime import date, datetime

import pytest

from project_decision.application.consequence_evaluation import (
    CONSEQUENCE_INSTRUCTIONS,
    CONSEQUENCE_LEVELS,
    ConsequenceEvaluation,
    consequence_question,
    consequence_of_delay_from_score,
    consequence_state,
    evaluate_session_consequences,
    score_session_with_evaluations,
)
from project_decision.application.jev_service import JevCallResult, send_jev_questions
from project_decision.application.planning_session import create_planning_session
from project_decision.application.priority_scoring import score_task
from project_decision.application.schedule_analysis import analyze_schedule
from project_decision.config import TypesafeSettings
from project_decision.domain import PlanningPeriod, ProjectState, Resource
from project_decision.infrastructure.jev.models import JevScoreAnswer
from tests.conftest import make_task
from tests.fakes.fake_type_safe import (
    FakeResponse,
    FakeScoreAnswer,
    FakeTypeSafeClient,
    fake_client_factory,
)

CAPTURED_AT = datetime(2026, 10, 21, 9, 0)
WEEK = PlanningPeriod(start_date=date(2026, 6, 1), end_date=date(2026, 6, 5))

SCORE_ANSWER = JevScoreAnswer(
    score=2.0,
    legend={0: "none", 1: "small", 2: "significant", 3: "major"},
    probabilities={0: 0.05, 1: 0.15, 2: 0.7, 3: 0.1},
    confidence=0.9,
)


def make_state(tasks) -> ProjectState:
    return ProjectState(
        project_id="P1",
        captured_at=CAPTURED_AT,
        project_name="Demo",
        tasks=list(tasks),
        resources=[Resource(id="R1", name="Alice")],
    )


def default_state() -> ProjectState:
    return make_state(
        [
            make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      notes="one", total_slack_days=0.0),
            make_task("B", start=date(2026, 6, 2), finish=date(2026, 6, 3),
                      notes="two", total_slack_days=0.0),
        ]
    )


def default_session() -> object:
    return create_planning_session(default_state(), WEEK)


def ok_call(score=2.0, confidence=0.9) -> JevCallResult:
    return JevCallResult(
        ok=True,
        model="jev-latest",
        request_id="req-1",
        answers={
            "consequence_of_delay": dc_replace(
                SCORE_ANSWER, score=score, confidence=confidence
            )
        },
    )


class RecordingSend:
    """A fake ``send_jev_questions``: records calls, returns a canned result."""

    def __init__(self, result):
        self.result = result
        self.calls: list[dict] = []

    def __call__(self, state, questions, **kwargs):
        self.calls.append({"state": state, "questions": questions, "kwargs": kwargs})
        return self.result


class TestQuestionShape:
    def test_the_score_question_has_four_situational_levels(self):
        question = consequence_question()

        assert question.instructions == CONSEQUENCE_INSTRUCTIONS
        assert question.criteria == list(CONSEQUENCE_LEVELS)
        assert len(question.criteria) == 4
        assert any("non-schedule" in criterion.lower() for criterion in question.criteria)
        assert "non-schedule" in question.instructions.lower()

    def test_question_and_state_never_see_schedule_information(self):
        # Schedule facts (slack, criticality, successors, dates, period,
        # resource conflicts, scores) must not enter Jev's input. The word
        # "resource" alone appears legitimately in the Level-3 situation
        # ("non-movable ... resource opportunity" — a business consequence).
        forbidden = (
            "total slack",
            "total_slack_days",
            "critical",
            "successor",
            "planning period",
            "priority score",
            "resource conflict",
        )
        body = repr([CONSEQUENCE_INSTRUCTIONS, *CONSEQUENCE_LEVELS]) + repr(
            consequence_state("Excavation", "neighborhood access permits required")
        )

        for word in forbidden:
            assert word.lower() not in body.lower(), word

    def test_state_contains_only_name_and_notes(self):
        state = consequence_state("Excavation", "permits required")

        assert state == {"task": {"name": "Excavation", "notes": "permits required"}}


class TestLevelToCDMapping:
    def test_documented_levels(self):
        assert consequence_of_delay_from_score(0) == pytest.approx(0.0)
        assert consequence_of_delay_from_score(1) == pytest.approx(1 / 3)
        assert consequence_of_delay_from_score(2) == pytest.approx(2 / 3)
        assert consequence_of_delay_from_score(3) == pytest.approx(1.0)

    def test_fractional_score_average_is_allowed(self):
        # The official Score answer is a probability-weighted level average,
        # so fractional scores stay valid.
        assert consequence_of_delay_from_score(1.5) == pytest.approx(0.5)

    def test_invalid_scores_are_rejected_not_clamped(self):
        for bad in (-1, 4, None, "two", True):
            with pytest.raises(ValueError):
                consequence_of_delay_from_score(bad)


class TestSingleEvaluation:
    def test_evaluated_result_preserves_jev_information(self):
        send = RecordingSend(ok_call(score=2.0, confidence=0.9))

        evaluation = evaluate_session_consequences(
            default_session(), default_state(), send=send
        )[0]

        assert evaluation.status == "evaluated"
        assert evaluation.consequence_of_delay == pytest.approx(2 / 3)
        assert evaluation.jev_score == 2.0
        assert evaluation.confidence == 0.9
        assert evaluation.probabilities == SCORE_ANSWER.probabilities
        assert evaluation.legend == SCORE_ANSWER.legend
        assert evaluation.review_reasons == ()
        # One Jev request per candidate, and A's state went to the first one.
        assert len(send.calls) == 2
        assert send.calls[0]["state"]["task"] == {"name": "Task A", "notes": "one"}

    def test_low_confidence_flags_review_but_keeps_cd(self):
        send = RecordingSend(ok_call(score=2.0, confidence=0.35))

        evaluation = evaluate_session_consequences(
            default_session(), default_state(), send=send
        )[0]

        assert evaluation.status == "evaluated"
        assert evaluation.consequence_of_delay == pytest.approx(2 / 3)  # unchanged
        assert evaluation.review_reasons
        assert "confidence" in evaluation.review_reasons[0]

    @pytest.mark.parametrize("bad_score", [-1, 4, None, "two"])
    def test_invalid_score_becomes_structured_failure(self, bad_score):
        send = RecordingSend(ok_call(score=bad_score))

        evaluation = evaluate_session_consequences(
            default_session(), default_state(), send=send
        )[0]

        assert evaluation.status == "failed"
        assert evaluation.consequence_of_delay is None
        assert any(
            "invalid consequence score" in reason for reason in evaluation.review_reasons
        )

    def test_jev_call_failure_is_structured_not_fatal(self):
        send = RecordingSend(
            JevCallResult(ok=False, error_message="connection refused", error_kind="connection")
        )

        evaluation = evaluate_session_consequences(
            default_session(), default_state(), send=send
        )[0]

        assert evaluation.status == "failed"
        assert evaluation.consequence_of_delay is None
        assert evaluation.error_message == "connection refused"
        assert evaluation.error_kind == "connection"
        assert any(
            "JEV evaluation failed: connection refused" in reason
            for reason in evaluation.review_reasons
        )

    def test_missing_answer_type_is_a_failure(self):
        send = RecordingSend(JevCallResult(ok=True, answers={}))

        evaluation = evaluate_session_consequences(
            default_session(), default_state(), send=send
        )[0]

        assert evaluation.status == "failed"
        assert "no answer returned" in evaluation.review_reasons[0]


class TestMissingNotes:
    def test_notes_missing_jev_is_not_called(self):
        send = RecordingSend(ok_call())
        state = make_state(
            [
                make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                          notes="one"),
                make_task("B", start=date(2026, 6, 1), finish=date(2026, 6, 2)),
            ]
        )
        session = create_planning_session(state, WEEK)

        evaluations = evaluate_session_consequences(session, state, send=send)

        # Only the notes-bearing task reached Jev.
        assert len(send.calls) == 1
        assert send.calls[0]["state"]["task"]["name"] == "Task A"
        noteless = evaluations[-1]
        assert noteless.status == "unavailable"
        assert noteless.consequence_of_delay is None
        assert any("notes are missing" in reason for reason in noteless.review_reasons)
        # The task WITH notes was evaluated normally.
        assert evaluations[0].status == "evaluated"


class TestBatchEvaluation:
    def test_order_follows_candidates_and_failures_are_isolated(self):
        state = make_state(
            [
                make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2), notes="one"),
                make_task("B", start=date(2026, 6, 2), finish=date(2026, 6, 3), notes="failing"),
                make_task("C", start=date(2026, 6, 3), finish=date(2026, 6, 4), notes="three"),
            ]
        )
        session = create_planning_session(state, WEEK)

        def send(jev_state, questions, **kwargs):
            if "failing" in repr(jev_state):
                return JevCallResult(ok=False, error_message="api down", error_kind="api")
            return ok_call()

        evaluations = evaluate_session_consequences(session, state, send=send)

        assert [e.task_id for e in evaluations] == ["A", "B", "C"]  # candidate order
        assert [e.status for e in evaluations] == ["evaluated", "failed", "evaluated"]
        assert evaluations[0].consequence_of_delay == pytest.approx(2 / 3)
        assert evaluations[1].error_message == "api down"

    def test_task_missing_from_state_is_marked_unavailable(self):
        state = default_state()
        session = create_planning_session(state, WEEK)
        empty_state = ProjectState(project_id="X", captured_at=CAPTURED_AT, project_name="X")

        evaluations = evaluate_session_consequences(
            session, empty_state, send=RecordingSend(ok_call())
        )

        assert all(e.status == "unavailable" for e in evaluations)
        assert all("not in the project state" in e.review_reasons[0] for e in evaluations)

    def test_evaluation_mutates_nothing(self):
        state = default_state()
        session = default_session()
        pristine_state = deepcopy(state)
        pristine_session = deepcopy(session)

        evaluate_session_consequences(session, state, send=RecordingSend(ok_call()))

        assert state == pristine_state
        assert session == pristine_session


class TestScoringIntegration:
    def test_evaluated_jev_value_flows_into_the_existing_score(self):
        state = default_state()
        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)
        evaluations = {
            "A": ConsequenceEvaluation(
                task_id="A", status="evaluated", consequence_of_delay=1.0
            )
        }

        by_task = {
            s.task_id: s for s in score_session_with_evaluations(session, analysis, evaluations)
        }

        assert by_task["A"].consequence_of_delay == pytest.approx(1.0)
        assert by_task["A"].consequence_source == "evaluated"
        assert not by_task["A"].needs_review
        # B/C keep score's own provisional path, including the range.
        assert by_task["B"].consequence_source == "provisional"
        assert by_task["B"].score_range is not None

    def test_scores_identical_to_direct_cd_input(self):
        state = default_state()
        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)
        evaluations = {
            "A": ConsequenceEvaluation(
                task_id="A", status="evaluated", consequence_of_delay=2 / 3
            )
        }

        via_jev = score_session_with_evaluations(session, analysis, evaluations)[0]
        direct = score_task(
            "A", WEEK, analysis.schedules["A"], consequence_of_delay=2 / 3
        )

        assert via_jev == direct

    def test_low_confidence_adds_review_but_not_a_changed_number(self):
        state = default_state()
        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)
        evaluations = {
            "A": ConsequenceEvaluation(
                task_id="A",
                status="evaluated",
                consequence_of_delay=2 / 3,
                confidence=0.35,
                review_reasons=(
                    "JEV confidence is low (0.35 < 0.5); manager review recommended. "
                    "The Jev value is still used in the score.",
                ),
            )
        }

        merged = score_session_with_evaluations(session, analysis, evaluations)[0]
        high_confidence = score_task(
            "A", WEEK, analysis.schedules["A"], consequence_of_delay=2 / 3
        )

        assert merged.total_score == high_confidence.total_score  # confidence NOT in the score
        assert merged.consequence_of_delay == pytest.approx(2 / 3)
        assert merged.needs_review
        assert any("confidence" in reason for reason in merged.review_reasons)

    def test_failure_cause_reaches_the_merged_score_reasons(self):
        state = default_state()
        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)
        evaluations = {
            "A": ConsequenceEvaluation(
                task_id="A",
                status="failed",
                error_message="api down",
                error_kind="api",
                review_reasons=("JEV evaluation failed: api down",),
            )
        }

        by_task = {
            s.task_id: s for s in score_session_with_evaluations(session, analysis, evaluations)
        }

        assert by_task["A"].consequence_source == "provisional"
        assert by_task["A"].total_score is not None  # provisional scoring intact
        assert by_task["A"].needs_review
        assert any(
            "JEV evaluation failed: api down" in reason for reason in by_task["A"].review_reasons
        )

    def test_missing_notes_cause_reaches_the_merged_score_reasons(self):
        state = default_state()
        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)
        evaluations = {
            "B": ConsequenceEvaluation(
                task_id="B",
                status="unavailable",
                review_reasons=(
                    "JEV evaluation unavailable: task notes are missing or insufficient.",
                ),
            )
        }

        scores = score_session_with_evaluations(session, analysis, evaluations)
        by_task = {s.task_id: s for s in scores}

        assert by_task["B"].consequence_source == "provisional"
        assert by_task["B"].needs_review
        assert any(
            "notes are missing" in reason for reason in by_task["B"].review_reasons
        )


class TestEndToEndWithRealServiceOffline:
    def test_full_stack_through_send_jev_questions_and_fake_sdk_client(self):
        # The REAL service boundary and client wrapper; only the SDK behind
        # them is faked, so no network call happens anywhere (the optional
        # live `pytest -m jev` test is untouched and still opt-in).
        settings = TypesafeSettings(api_key="sk-offline-test-key")
        client = FakeTypeSafeClient(
            response=FakeResponse(
                {
                    "consequence_of_delay": FakeScoreAnswer(
                        3.0,
                        {0: "none", 1: "small", 2: "significant", 3: "major"},
                        {0: 0.02, 1: 0.08, 2: 0.2, 3: 0.7},
                        0.8,
                    )
                }
            )
        )
        state = make_state(
            [
                make_task(
                    "A",
                    start=date(2026, 6, 1),
                    finish=date(2026, 6, 2),
                    notes="blasting permit required in city center",
                )
            ]
        )
        session = create_planning_session(state, WEEK)

        evaluations = evaluate_session_consequences(
            session,
            state,
            send=send_jev_questions,
            settings=settings,
            client_factory=fake_client_factory(client),
        )

        assert client.calls, "the question reached the (fake) SDK client"
        assert client.calls[0]["state"] == {
            "task": {
                "name": "Task A",
                "notes": "blasting permit required in city center",
            }
        }
        assert "sk-offline-test-key" not in repr(client.calls[0])  # key never exposed
        (evaluation,) = evaluations
        assert evaluation.status == "evaluated"
        assert evaluation.consequence_of_delay == pytest.approx(1.0)
        assert evaluation.review_reasons == ()  # confidence 0.8 >= 0.5
