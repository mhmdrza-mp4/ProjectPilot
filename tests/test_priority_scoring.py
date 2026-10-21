"""Tests for the deterministic priority-scoring core (no JEV, no network, no MSP)."""

from copy import deepcopy
from datetime import date, datetime

import pytest

from project_decision.application.priority_scoring import (
    NEUTRAL_CONSEQUENCE,
    ScoringParameters,
    TaskPriorityScore,
    score_planning_session,
    score_task,
    schedule_pressure,
    successor_impact,
)
from project_decision.application.schedule_analysis import TaskSchedule, analyze_schedule
from project_decision.application.planning_session import (
    create_planning_session,
    remove_candidate,
)
from project_decision.domain import PlanningPeriod, ProjectState, Resource
from tests.conftest import make_task

CAPTURED_AT = datetime(2026, 10, 21, 9, 0)

# Mon 1 - Fri 5 June 2026 => H = 5
WEEK = PlanningPeriod(start_date=date(2026, 6, 1), end_date=date(2026, 6, 5))
# Mon 1 - Fri 12 June 2026 => H = 10
TWO_WEEKS = PlanningPeriod(start_date=date(2026, 6, 1), end_date=date(2026, 6, 12))


def make_state() -> ProjectState:
    """Three overlapping candidates with slack 0 (maximum pressure)."""
    return ProjectState(
        project_id="P1",
        captured_at=CAPTURED_AT,
        project_name="Demo",
        tasks=[
            make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 3), total_slack_days=0.0),
            make_task("B", start=date(2026, 6, 2), finish=date(2026, 6, 4), total_slack_days=0.0),
            make_task("C", start=date(2026, 6, 3), finish=date(2026, 6, 5), total_slack_days=0.0),
        ],
        resources=[Resource(id="R1", name="Alice")],
    )

SCHEDULE = TaskSchedule(
    task_id="A",
    successor_ids=("B",),
    successor_count=1,
    total_slack_days=0.0,
    is_critical=True,
)


def schedule_for(successors: int, slack: float | None = 0.0, critical=False):
    return TaskSchedule(
        task_id="A",
        successor_ids=tuple(f"S{i}" for i in range(successors)),
        successor_count=successors,
        total_slack_days=slack,
        is_critical=critical,
    )


class TestSchedulePressureFormula:
    def test_h_ten_examples(self):
        for slack, expected in [
            (-20, 1.0), (-5, 1.0), (0.0, 1.0),
            (1, 0.9), (3, 0.7), (7, 0.3),
            (10, 0.0), (20, 0.0), (15, 0.0),
        ]:
            assert schedule_pressure(slack, 10) == pytest.approx(expected), slack

    def test_missing_slack_is_none_never_invented(self):
        assert schedule_pressure(None, 10) is None

    def test_non_positive_horizon_is_none(self):
        assert schedule_pressure(0.0, 0) is None
        assert schedule_pressure(5.0, -2) is None

    def test_h_come_from_the_planning_period(self):
        # H=5 week: slack 1 -> 1 - 1/5 = 0.8.
        score = score_task("A", WEEK, schedule_for(0, 1))
        assert score.schedule_pressure == pytest.approx(0.8)

    def test_explicit_horizon_overrides_the_period(self):
        params = ScoringParameters(horizon=10)
        score = score_task("A", WEEK, schedule_for(0, 4), parameters=params)
        # H=10: slack 4 -> 0.6 (not H=5 -> 0.2).
        assert score.schedule_pressure == pytest.approx(0.6)


class TestSuccessorImpactFormula:
    def test_k_five_examples(self):
        for count, expected in [(0, 0.0), (1, 0.2), (3, 0.6), (5, 1.0), (10, 1.0)]:
            assert successor_impact(count, 5) == pytest.approx(expected)

    def test_missing_successor_data_is_none(self):
        assert successor_impact(None, 5) is None


class TestConsequenceInput:
    def test_valid_values_accepted_unchanged(self):
        for value in [0.0, 1 / 3, 2 / 3, 1.0]:
            score = score_task("A", WEEK, SCHEDULE, consequence_of_delay=value)
            assert score.consequence_of_delay == pytest.approx(value)
            assert score.consequence_source == "evaluated"
            assert not score.needs_review

    def test_out_of_range_values_are_rejected(self):
        for bad in (-0.1, 1.1, 2):
            with pytest.raises(ValueError, match=r"\[0, 1\]"):
                score_task("A", WEEK, SCHEDULE, consequence_of_delay=bad)

    def test_non_numeric_input_rejected(self):
        with pytest.raises(ValueError, match=r"number in \[0, 1\]"):
            score_task("A", WEEK, SCHEDULE, consequence_of_delay="half")
        with pytest.raises(ValueError, match=r"number in \[0, 1\]"):
            score_task("A", WEEK, SCHEDULE, consequence_of_delay=True)


class TestScoringParameters:
    def test_documented_defaults(self):
        p = ScoringParameters()
        assert p.successor_cap == 5
        assert p.consequence_max_level == 3
        assert p.schedule_pressure_weight == pytest.approx(0.40)
        assert p.successor_impact_weight == pytest.approx(0.20)
        assert p.consequence_of_delay_weight == pytest.approx(0.40)
        assert p.horizon is None

    def test_fractional_weights_within_tolerance_accept(self):
        p = ScoringParameters(
            schedule_pressure_weight=1 / 3,
            successor_impact_weight=1 / 3,
            consequence_of_delay_weight=1 / 3,
        )
        assert p.schedule_pressure_weight == pytest.approx(1 / 3)

    def test_sum_not_one_rejected_not_normalized(self):
        with pytest.raises(ValueError, match="sum to 1"):
            ScoringParameters(
                schedule_pressure_weight=0.40,
                successor_impact_weight=0.20,
                consequence_of_delay_weight=0.30,
            )

    def test_negative_weight_rejected(self):
        with pytest.raises(ValueError, match="non-negative"):
            ScoringParameters(
                schedule_pressure_weight=-0.4,
                successor_impact_weight=0.2,
                consequence_of_delay_weight=1.2,
            )

    def test_misc_validation(self):
        with pytest.raises(ValueError, match="successor_cap"):
            ScoringParameters(successor_cap=0)
        with pytest.raises(ValueError, match="consequence_max_level"):
            ScoringParameters(consequence_max_level=0)
        with pytest.raises(ValueError, match="horizon"):
            ScoringParameters(horizon=0)


class TestWeightedScore:
    def test_documented_example_equal_seventy(self):
        # SP=1, SI=0.5, CD=0.5 => 100*(0.4 + 0.1 + 0.2) = 70.
        # SI=0.5 with three successors needs K=6 (the example's SI value
        # is a criterion value, not bound to the default cap).
        score = score_task(
            "A",
            TWO_WEEKS,
            schedule_for(3, 0.0),
            consequence_of_delay=0.5,
            parameters=ScoringParameters(successor_cap=6),
        )
        assert score.successor_impact == pytest.approx(0.5)
        assert score.total_score == pytest.approx(70.0)
        assert score.consequence_source == "evaluated"
        assert not score.needs_review

    def test_extreme_values(self):
        all_low = score_task("A", TWO_WEEKS, schedule_for(0, 100.0), 0.0)
        all_high = score_task("A", TWO_WEEKS, schedule_for(10, 0.0), 1.0)
        assert all_low.total_score == pytest.approx(0.0)
        assert all_high.total_score == pytest.approx(100.0)


class TestContributions:
    def test_contributions_sum_to_total(self):
        for slack, successors, cd in [
            (-5.0, 2, 0.0), (0.0, 0, 1.0), (2.0, 4, 0.5), (1.0, 1, 1 / 3),
        ]:
            score = score_task(
                "A", TWO_WEEKS, schedule_for(successors, slack), consequence_of_delay=cd
            )
            contributions = (
                score.schedule_contribution
                + score.successor_contribution
                + score.consequence_contribution
            )
            assert score.total_score == pytest.approx(contributions), (slack, successors, cd)

    def test_weights_are_not_renormalized_when_a_criterion_is_missing(self):
        # Slack missing: successor + consequence still use their FULL weights.
        score = score_task(
            "A", TWO_WEEKS, schedule_for(5, None), consequence_of_delay=1.0
        )
        assert score.total_score is None
        assert score.schedule_pressure is None
        assert score.schedule_contribution is None
        assert score.successor_contribution == pytest.approx(100.0 * 0.2 * 1.0)
        assert score.consequence_contribution == pytest.approx(100.0 * 0.4 * 1.0)


class TestMissingConsequence:
    def test_provisional_neutral_value_used_and_flagged(self):
        score = score_task("A", TWO_WEEKS, schedule_for(3, 0.0), consequence_of_delay=None)

        assert score.consequence_of_delay == pytest.approx(NEUTRAL_CONSEQUENCE)
        assert score.consequence_source == "provisional"
        assert score.needs_review
        assert any("not evaluated" in reason for reason in score.review_reasons)

    def test_score_range_from_cd_zero_to_one(self):
        score = score_task("A", TWO_WEEKS, schedule_for(3, 0.0), consequence_of_delay=None)

        # fixed = 100*(0.4*1 + 0.2*0.6) = 52 => range 52..92; CD=0.5 gives 72.
        assert score.total_score == pytest.approx(72.0)
        assert score.score_range is not None
        assert score.score_range.minimum == pytest.approx(52.0)
        assert score.score_range.maximum == pytest.approx(92.0)

    def test_range_not_claimed_when_other_criteria_missing(self):
        # Without a base score, a CD range would be misleading.
        score = score_task(
            "A", TWO_WEEKS, schedule_for(3, None), consequence_of_delay=None
        )
        assert score.score_range is None
        assert score.total_score is None


class TestMissingSlack:
    def test_missing_slack_marked_review_no_fake_value(self):
        score = score_task("A", TWO_WEEKS, schedule_for(0, None), consequence_of_delay=0.5)

        assert score.schedule_pressure is None
        assert score.total_score is None
        assert score.needs_review
        assert any("slack" in reason for reason in score.review_reasons)
        assert any("total_slack_days" in reason for reason in score.review_reasons)

    def test_zero_slack_is_data_not_missing(self):
        # 0.0 is a real (maximum-pressure) observation, not absence.
        score = score_task("A", TWO_WEEKS, schedule_for(0, 0.0), consequence_of_delay=0.5)
        assert score.schedule_pressure == pytest.approx(1.0)


class TestMissingScheduleData:
    def test_entire_schedule_view_missing_marks_review(self):
        score = score_task("X", TWO_WEEKS, None, consequence_of_delay=0.5)

        assert score.schedule_pressure is None
        assert score.successor_impact is None
        assert score.total_score is None
        assert score.is_critical is None
        assert score.needs_review
        assert any("no schedule-analysis data" in reason for reason in score.review_reasons)


class TestCriticalityHasNoNumericEffect:
    @pytest.mark.parametrize("critical", [False, True])
    def test_only_displays_never_scores(self, critical):
        # SP=1 (slack 0), SI=0.6 (3 successors), CD=0.5 -> 72. The only
        # thing asserted is that BOTH criticality values give the same
        # number (criticality is display-only).
        score = score_task(
            "A",
            TWO_WEEKS,
            schedule_for(3, 0.0, critical=critical),
            consequence_of_delay=0.5,
        )
        assert score.total_score == pytest.approx(72.0)
        assert score.is_critical is critical


class TestSessionScoring:
    def test_candidates_scored_in_session_order(self):
        state = make_state()
        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)

        scores = score_planning_session(session, analysis, consequence_of_delay=0.5)

        assert [s.task_id for s in scores] == list(session.candidate_task_ids)
        assert all(isinstance(s, TaskPriorityScore) for s in scores)

    def test_per_task_consequence_mapping(self):
        state = make_state()
        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)

        scores = score_planning_session(
            session,
            analysis,
            consequence_of_delay={"A": 1.0},  # B and C count as NOT evaluated
        )
        by_task = {s.task_id: s for s in scores}
        assert by_task["A"].consequence_source == "evaluated"
        assert by_task["B"].consequence_source == "provisional"
        assert by_task["C"].consequence_source == "provisional"
        # B: SP=1 -> 40 fixed, provisional CD 0.5 -> total 60, range 40..80.
        assert by_task["B"].total_score == pytest.approx(60.0)
        assert by_task["B"].score_range is not None
        assert by_task["B"].score_range.minimum == pytest.approx(40.0)
        assert by_task["B"].score_range.maximum == pytest.approx(80.0)

    def test_candidate_independence(self):
        state = make_state()
        analysis = analyze_schedule(state)
        base_session = remove_candidate(create_planning_session(state, WEEK), "C")
        full_session = create_planning_session(state, WEEK)  # A, B, C

        base = {s.task_id: s for s in score_planning_session(
            base_session, analysis, consequence_of_delay={"A": 1.0}
        )}
        extended = {s.task_id: s for s in score_planning_session(
            full_session, analysis, consequence_of_delay={"A": 1.0}
        )}

        assert list(base) == ["A", "B"]
        # Adding C to the candidate set changed neither A's nor B's score.
        assert base["A"] == extended["A"]
        assert base["B"] == extended["B"]

    def test_candidate_missing_from_analysis_is_not_fabricated(self):
        state = make_state()
        session = create_planning_session(state, WEEK)
        empty_analysis = analyze_schedule(ProjectState(
            project_id="X", captured_at=CAPTURED_AT, project_name="Empty"
        ))

        scores = score_planning_session(session, empty_analysis, consequence_of_delay=0.5)

        assert all(s.total_score is None and s.needs_review for s in scores)
        assert all(
            any("no schedule-analysis data" in r for r in s.review_reasons)
            for s in scores
        )


class TestImmutability:
    def test_scoring_mutates_nothing(self):
        state = make_state()
        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)

        pristine_state = deepcopy(state)
        pristine_session = deepcopy(session)
        pristine_analysis = deepcopy(analysis)
        pristine_period = deepcopy(WEEK)

        score_planning_session(
            session, analysis, consequence_of_delay={"A": 1.0},
            parameters=ScoringParameters(horizon=8),
        )

        assert state == pristine_state
        assert session == pristine_session
        assert analysis == pristine_analysis
        assert WEEK == pristine_period
