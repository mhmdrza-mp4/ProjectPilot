"""Tests for deterministic priority ranking / DecisionResult (offline)."""

from copy import deepcopy
from datetime import date, datetime

import pytest

from project_decision.application.planning_session import (
    add_candidate,
    create_planning_session,
)
from project_decision.application.priority_ranking import (
    DecisionResult,
    RankedTask,
    create_decision_result,
)
from project_decision.application.priority_scoring import (
    ScoringParameters,
    ScoreRange,
    TaskPriorityScore,
    score_planning_session,
)
from project_decision.application.resource_conflicts import (
    ConflictStatus,
    analyze_resource_conflicts,
)
from project_decision.application.schedule_analysis import (
    ScheduleAnalysis,
    TaskSchedule,
    analyze_schedule,
)
from project_decision.domain import ProjectState, Resource
from project_decision.domain.planning_period import PlanningPeriod
from tests.conftest import make_task

CAPTURED_AT = datetime(2026, 10, 21, 9, 0)
WEEK = PlanningPeriod(start_date=date(2026, 6, 1), end_date=date(2026, 6, 5))


def make_state(tasks, resources=()) -> ProjectState:
    return ProjectState(
        project_id="P1",
        captured_at=CAPTURED_AT,
        project_name="Demo",
        tasks=list(tasks),
        resources=list(resources),
    )


def scheduled(task_id, *, slack=0.0, successors=0, successor_ids=(), critical=False):
    return TaskSchedule(
        task_id=task_id,
        successor_ids=successor_ids,
        successor_count=successors,
        total_slack_days=slack,
        is_critical=critical,
    )


def scored(
    task_id,
    *,
    total=50.0,
    cd=0.5,
    source="provisional",
    needs_review=False,
    reasons=(),
    score_range=None,
    critical=False,
):
    """Direct TaskPriorityScore for ranking-rule unit tests."""
    return TaskPriorityScore(
        task_id=task_id,
        schedule_pressure=None if total is None else 0.4,
        successor_impact=None if total is None else 0.5,
        consequence_of_delay=cd,
        consequence_source=source,
        schedule_contribution=None,
        successor_contribution=None,
        consequence_contribution=None,
        total_score=total,
        needs_review=needs_review,
        review_reasons=tuple(reasons),
        score_range=score_range,
        is_critical=critical,
    )


def rank_ids(result: DecisionResult) -> list[str]:
    return [ranked.task_id for ranked in result.ranked_tasks]


def simple_fixture(task_ids, schedules, scores, *, extra_state_task_ids=()):
    """State + session + analysis + scores with explicit candidate control."""
    tasks = [
        make_task(
            task_id,
            start=date(2026, 6, 1),
            finish=date(2026, 6, 2),
        )
        for task_id in (*task_ids, *extra_state_task_ids)
    ]
    state = make_state(tasks)
    session = create_planning_session(
        state, WEEK, candidate_task_ids=tuple(task_ids)
    )
    analysis = ScheduleAnalysis(schedules=dict(schedules), issues=())
    return state, session, analysis, scores


class TestOrderingRules:
    def test_basic_ranking_by_score(self):
        state, session, analysis, scores = simple_fixture(
            ("A", "B", "C"),
            {
                "A": scheduled("A", slack=2.0),
                "B": scheduled("B", slack=2.0),
                "C": scheduled("C", slack=2.0),
            },
            [
                scored("A", total=80.0),
                scored("B", total=60.0),
                scored("C", total=70.0),
            ],
        )

        result = create_decision_result(session, state, analysis, scores)

        assert rank_ids(result) == ["A", "C", "B"]
        assert [ranked.rank for ranked in result.ranked_tasks] == [1, 2, 3]

    def test_rounded_scores_tie_then_slack_breaks_it(self):
        # round(80.44, 1) == round(80.43, 1) == 80.4: a genuine tie. The
        # slack decides (A 2.0 < B 5.0), not the unrounded difference.
        state, session, analysis, scores = simple_fixture(
            ("A", "B"),
            {
                "A": scheduled("A", slack=5.0),
                "B": scheduled("B", slack=2.0),
            },
            [
                scored("A", total=80.44),
                scored("B", total=80.43),
            ],
        )

        result = create_decision_result(session, state, analysis, scores)

        assert rank_ids(result) == ["B", "A"]  # lower slack first
        assert result.ranked_tasks[0].total_score == pytest.approx(80.43)
        assert all(ranked.total_score is not None for ranked in result.ranked_tasks)

    def test_slack_tiebreak_only_when_scores_tie(self):
        state, session, analysis, scores = simple_fixture(
            ("A", "B"),
            {
                "A": scheduled("A", slack=2.0),
                "B": scheduled("B", slack=5.0),
            },
            [
                scored("A", total=70.0),
                scored("B", total=70.0),
            ],
        )

        result = create_decision_result(session, state, analysis, scores)

        assert rank_ids(result) == ["A", "B"]

    def test_higher_slack_does_not_beat_lower_score(self):
        # Slack is ONLY a tie-breaker: it must not outrank the score.
        state, session, analysis, scores = simple_fixture(
            ("A", "B"),
            {
                "A": scheduled("A", slack=0.0),
                "B": scheduled("B", slack=100.0),
            },
            [
                scored("A", total=60.0),
                scored("B", total=70.0),
            ],
        )

        result = create_decision_result(session, state, analysis, scores)

        assert rank_ids(result) == ["B", "A"]

    def test_successor_tiebreak(self):
        state, session, analysis, scores = simple_fixture(
            ("A", "B"),
            {
                "A": scheduled("A", slack=2.0, successors=4, successor_ids=("S1", "S2", "S3", "S4")),
                "B": scheduled("B", slack=2.0, successors=2, successor_ids=("S5", "S6")),
            },
            [scored("A", total=70.0), scored("B", total=70.0)],
        )

        result = create_decision_result(session, state, analysis, scores)

        assert rank_ids(result) == ["A", "B"]  # more direct successors first

    def test_consequence_tiebreak(self):
        state, session, analysis, scores = simple_fixture(
            ("A", "B"),
            {
                "A": scheduled("A", slack=2.0),
                "B": scheduled("B", slack=2.0),
            },
            [
                scored("A", total=70.0, cd=1.0, source="evaluated"),
                scored("B", total=70.0, cd=0.5, source="evaluated"),
            ],
        )

        result = create_decision_result(session, state, analysis, scores)

        assert rank_ids(result) == ["A", "B"]  # higher evaluated CD first

    def test_final_stable_task_id_tiebreak(self):
        state, session, analysis, scores = simple_fixture(
            ("task-B", "task-A"),
            {
                "task-B": scheduled("task-B", slack=2.0),
                "task-A": scheduled("task-A", slack=2.0),
            },
            [
                scored("task-B", total=70.0),
                scored("task-A", total=70.0),
            ],
        )

        result = create_decision_result(session, state, analysis, scores)

        assert rank_ids(result) == ["task-A", "task-B"]  # id ascending, input order ignored

    def test_sequential_ranks_for_four_identical_tasks(self):
        ids = ("A", "B", "C", "D")
        state, session, analysis, scores = simple_fixture(
            ids,
            {task_id: scheduled(task_id, slack=2.0) for task_id in ids},
            [scored(task_id, total=70.0) for task_id in ids],
        )

        result = create_decision_result(session, state, analysis, scores)

        assert rank_ids(result) == ["A", "B", "C", "D"]  # id order, never 1,1,3
        assert [ranked.rank for ranked in result.ranked_tasks] == [1, 2, 3, 4]


class TestMissingTieBreakers:
    def test_known_slack_sorts_before_missing_slack(self):
        # Unit-level: equal totals, one schedule view without slack.
        state, session, analysis, scores = simple_fixture(
            ("A", "B"),
            {
                "A": scheduled("A", slack=10.0),
                "B": scheduled("B", slack=None),
            },
            [scored("A", total=70.0), scored("B", total=70.0)],
        )

        result = create_decision_result(session, state, analysis, scores)

        assert rank_ids(result) == ["A", "B"]  # known before missing, never a fake 0

    def test_missing_schedule_view_sorts_after_known_successors(self):
        # Task B has NO schedule view at all: successor data is missing,
        # not zero — it ranks after A at the successor tier.
        state, session, analysis, scores = simple_fixture(
            ("A", "B"),
            {"A": scheduled("A", slack=2.0, successors=3, successor_ids=("S1", "S2", "S3"))},
            [scored("A", total=70.0), scored("B", total=70.0)],
        )

        result = create_decision_result(session, state, analysis, scores)

        assert rank_ids(result) == ["A", "B"]
        missing = result.ranked_tasks[1]
        assert missing.total_slack_days is None
        assert missing.successor_count is None
        assert missing.is_critical is None

    def test_provisional_cd_sorts_after_evaluated_cd(self):
        # Provisional 0.5 is NOT used as if observed: evaluated wins even
        # at CD 0.0 ("known before missing"), per the documented rule.
        state, session, analysis, scores = simple_fixture(
            ("A", "B"),
            {
                "A": scheduled("A", slack=2.0),
                "B": scheduled("B", slack=2.0),
            },
            [
                scored("A", total=70.0, cd=0.0, source="evaluated"),
                scored("B", total=70.0, cd=0.5, source="provisional"),
            ],
        )

        result = create_decision_result(session, state, analysis, scores)

        assert rank_ids(result) == ["A", "B"]

    def test_provisional_ties_fall_through_to_next_criterion(self):
        # Both provisional at the CD tier → equal there → slack decides.
        state, session, analysis, scores = simple_fixture(
            ("A", "B"),
            {
                "A": scheduled("A", slack=7.0),
                "B": scheduled("B", slack=2.0),
            },
            [
                scored("A", total=70.0, cd=0.5, source="provisional"),
                scored("B", total=70.0, cd=0.5, source="provisional"),
            ],
        )

        result = create_decision_result(session, state, analysis, scores)

        assert rank_ids(result) == ["B", "A"]  # lower slack first via fall-through

    def test_unscored_candidate_is_ranked_last_and_never_fabricated(self):
        state, session, analysis, scores = simple_fixture(
            ("A", "B"),
            {
                "A": scheduled("A", slack=2.0),
                "B": scheduled("B", slack=2.0),
            },
            [scored("A", total=70.0)],  # B never scored
        )

        result = create_decision_result(session, state, analysis, scores)

        assert rank_ids(result) == ["A", "B"]
        unscored = result.ranked_tasks[1]
        assert unscored.score is None
        assert unscored.total_score is None
        assert unscored.needs_review is True
        assert unscored.criterion_contributions == (None, None, None)


class TestReviewStatusAndDisplay:
    def test_needs_review_task_can_rank_first(self):
        state, session, analysis, scores = simple_fixture(
            ("A", "B"),
            {
                "A": scheduled("A", slack=2.0),
                "B": scheduled("B", slack=2.0),
            },
            [
                scored(
                    "A",
                    total=90.0,
                    needs_review=True,
                    reasons=("JEV confidence is low (0.3 < 0.5)",),
                ),
                scored("B", total=40.0),
            ],
        )

        result = create_decision_result(session, state, analysis, scores)

        assert rank_ids(result) == ["A", "B"]  # review status never blocks rank 1
        assert result.ranked_tasks[0].needs_review is True
        assert result.ranked_tasks[1].needs_review is False

    def test_review_reasons_preserved_exactly_not_flattened(self):
        reasons = ("schedule pressure is unavailable: no slack value was "
                   "reported (total_slack_days missing)",
                   "JEV evaluation unavailable: task notes are missing or insufficient.")
        state, session, analysis, scores = simple_fixture(
            ("A",),
            {"A": scheduled("A", slack=None)},
            [scored("A", total=None, needs_review=True, reasons=reasons)],
        )

        result = create_decision_result(session, state, analysis, scores)

        assert result.ranked_tasks[0].review_reasons == reasons

    def test_provisional_score_range_preserved_unchanged(self):
        expected_range = ScoreRange(minimum=60.0, maximum=100.0)
        state, session, analysis, scores = simple_fixture(
            ("A",),
            {"A": scheduled("A", slack=2.0)},
            [scored("A", total=80.0, source="provisional", score_range=expected_range)],
        )

        result = create_decision_result(session, state, analysis, scores)

        assert result.ranked_tasks[0].score_range == expected_range

    def test_criticality_never_affects_ranking(self):
        # Critical A ranks BELOW non-critical B because its score is lower.
        state, session, analysis, scores = simple_fixture(
            ("A", "B"),
            {
                "A": scheduled("A", slack=2.0, critical=True),
                "B": scheduled("B", slack=2.0, critical=False),
            },
            [scored("A", total=40.0, critical=True), scored("B", total=80.0)],
        )

        result = create_decision_result(session, state, analysis, scores)

        assert rank_ids(result) == ["B", "A"]
        assert result.ranked_tasks[1].is_critical is True  # display-only passthrough

    def test_ranked_task_exposes_the_scoring_surface(self):
        state, session, analysis, scores = simple_fixture(
            ("A",),
            {"A": scheduled("A", slack=2.0)},
            [scored("A", total=80.0, cd=0.75, source="evaluated")],
        )

        ranked = create_decision_result(session, state, analysis, scores).ranked_tasks[0]

        assert ranked.task_name == "Task A"
        assert ranked.total_score == pytest.approx(80.0)
        assert ranked.consequence_of_delay == pytest.approx(0.75)
        assert ranked.consequence_source == "evaluated"
        assert ranked.schedule_pressure == pytest.approx(0.4)
        assert ranked.successor_impact == pytest.approx(0.5)
        assert ranked.score == scores[0]  # composition, not a copy


class TestResourceWarnings:
    def conflicting_fixture(self):
        tasks = [
            make_task(
                "A",
                start=date(2026, 6, 1),
                finish=date(2026, 6, 2),
                assigned_resource_ids=("R1",),
            ),
            make_task(
                "B",
                start=date(2026, 6, 2),
                finish=date(2026, 6, 3),
                assigned_resource_ids=("R1",),
            ),
            make_task(
                "C",
                start=date(2026, 6, 1),
                finish=date(2026, 6, 2),
                assigned_resource_ids=("R2",),
            ),
        ]
        state = make_state(tasks, resources=[Resource(id="R1", name="Alice"),
                                             Resource(id="R2", name="Bob")])
        session = create_planning_session(state, WEEK, candidate_task_ids=("A", "B", "C"))
        analysis = ScheduleAnalysis(
            schedules={
                "A": scheduled("A", slack=2.0),
                "B": scheduled("B", slack=2.0),
                "C": scheduled("C", slack=2.0),
            },
            issues=(),
        )
        scores = [scored("A", total=90.0), scored("B", total=70.0), scored("C", total=50.0)]
        return state, session, analysis, scores

    def test_warning_attached_with_shared_resources(self):
        state, session, analysis, scores = self.conflicting_fixture()
        conflicts = analyze_resource_conflicts(state)

        result = create_decision_result(session, state, analysis, scores, conflicts)

        a, b, c = result.ranked_tasks
        assert a.resource_warning is True
        assert a.shared_resource_ids == ("R1",)
        assert a.shared_resource_names == ("Alice",)
        assert b.resource_warning is True and b.shared_resource_ids == ("R1",)
        assert c.resource_warning is False  # R2 has no overlap partner

    def test_warning_does_not_change_scores_or_ranking(self):
        state, session, analysis, scores = self.conflicting_fixture()
        conflicts = analyze_resource_conflicts(state)

        with_warnings = create_decision_result(session, state, analysis, scores, conflicts)
        without_warnings = create_decision_result(session, state, analysis, scores)

        assert [
            (ranked.rank, ranked.task_id, ranked.total_score, ranked.needs_review)
            for ranked in with_warnings.ranked_tasks
        ] == [
            (ranked.rank, ranked.task_id, ranked.total_score, ranked.needs_review)
            for ranked in without_warnings.ranked_tasks
        ]

    def test_unknown_conflict_pair_does_not_claim_a_warning(self):
        tasks = [
            make_task("A", start=None, finish=None, assigned_resource_ids=("R1",)),
            make_task("B", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      assigned_resource_ids=("R1",)),
        ]
        state = make_state(tasks, resources=[Resource(id="R1", name="Alice")])
        session = create_planning_session(state, WEEK, candidate_task_ids=("A", "B"))
        conflicts = analyze_resource_conflicts(state)
        assert conflicts.conflicts[0].status is ConflictStatus.UNKNOWN

        result = create_decision_result(
            session,
            state,
            ScheduleAnalysis(schedules={}, issues=()),
            [],
            conflicts,
        )

        assert all(ranked.resource_warning is False for ranked in result.ranked_tasks)

    def test_task_in_two_pairs_aggregates_without_duplicates(self):
        tasks = [
            make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      assigned_resource_ids=("R1", "R2")),
            make_task("B", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      assigned_resource_ids=("R1",)),
            make_task("C", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      assigned_resource_ids=("R2",)),
        ]
        state = make_state(tasks, resources=[Resource(id="R1", name="Alice"),
                                             Resource(id="R2", name="Alicia")])
        session = create_planning_session(state, WEEK, candidate_task_ids=("A", "B", "C"))
        conflicts = analyze_resource_conflicts(state)

        result = create_decision_result(
            session,
            state,
            ScheduleAnalysis(schedules={}, issues=()),
            [],
            conflicts,
        )

        a = result.ranked_tasks[0]
        assert a.shared_resource_ids == ("R1", "R2")  # analysis order, deduplicated
        assert a.shared_resource_names == ("Alice", "Alicia")

    def test_callout_lists_high_priority_conflicted_tasks_in_rank_order(self):
        state, session, analysis, scores = self.conflicting_fixture()

        result = create_decision_result(
            session, state, analysis, scores, analyze_resource_conflicts(state)
        )

        callout = result.tasks_with_resource_warnings()
        assert [ranked.task_id for ranked in callout] == ["A", "B"]  # rank order
        assert callout[0].rank == 1

    def test_no_conflict_analysis_means_no_warnings(self):
        state, session, analysis, scores = self.conflicting_fixture()

        result = create_decision_result(session, state, analysis, scores, None)

        assert all(ranked.resource_warning is False for ranked in result.ranked_tasks)


class TestScopingAndPurity:
    def test_only_session_candidates_are_ranked(self):
        state, session, analysis, scores = simple_fixture(
            ("A", "B"),
            {
                "A": scheduled("A", slack=2.0),
                "B": scheduled("B", slack=2.0),
                "Z": scheduled("Z", slack=0.0),
            },
            [
                scored("A", total=90.0),
                scored("B", total=40.0),
                scored("Z", total=999.0),  # highest score — still ignored
            ],
            extra_state_task_ids=("Z",),
        )

        result = create_decision_result(session, state, analysis, scores)

        assert rank_ids(result) == ["A", "B"]  # Z never enters the result

    def test_manual_candidate_is_ranked_like_any_candidate(self):
        tasks = [
            make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2)),
            make_task(
                "OUT",  # finished long before the period → not a default candidate
                start=date(2026, 1, 1),
                finish=date(2026, 1, 2),
            ),
        ]
        state = make_state(tasks)
        base_session = create_planning_session(state, WEEK)  # A only
        base_ids = [ranked.task_id for ranked in
                    create_decision_result(
                        base_session, state,
                        ScheduleAnalysis(schedules={}, issues=()), []
                    ).ranked_tasks]
        assert base_ids == ["A"]

        manual_session = add_candidate(state, base_session, "OUT")
        analysis = ScheduleAnalysis(
            schedules={
                "A": scheduled("A", slack=2.0),
                "OUT": scheduled("OUT", slack=0.0),
            },
            issues=(),
        )
        result = create_decision_result(
            manual_session,
            state,
            analysis,
            [scored("A", total=50.0), scored("OUT", total=90.0)],
        )

        assert rank_ids(result) == ["OUT", "A"]  # ranked exactly by the same rules

    def test_result_is_identical_across_runs_and_input_orders(self):
        ids = ("A", "B", "C")
        state, session, analysis, scores = simple_fixture(
            ids,
            {task_id: scheduled(task_id, slack=2.0) for task_id in ids},
            [scored("A", total=70.0), scored("B", total=80.0), scored("C", total=70.0)],
        )

        first = create_decision_result(session, state, analysis, scores)
        second = create_decision_result(session, state, analysis, list(reversed(scores)))

        assert first == second

    def test_ranking_mutates_no_input(self):
        state, session, analysis, scores = self._full_fixture()
        conflicts = analyze_resource_conflicts(state)
        pristine = (
            deepcopy(state),
            deepcopy(session),
            deepcopy(analysis),
            deepcopy(scores),
            deepcopy(conflicts),
        )

        create_decision_result(session, state, analysis, scores, conflicts)

        assert (
            state,
            session,
            analysis,
            scores,
            conflicts,
        ) == pristine

    def _full_fixture(self):
        state, session, analysis, scores = self._two_task_state()
        return state, session, analysis, scores

    def _two_task_state(self):
        tasks = [
            make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      assigned_resource_ids=("R1",)),
            make_task("B", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      assigned_resource_ids=("R1",)),
        ]
        state = make_state(tasks, resources=[Resource(id="R1", name="Alice")])
        session = create_planning_session(state, WEEK, candidate_task_ids=("A", "B"))
        analysis = ScheduleAnalysis(
            schedules={
                "A": scheduled("A", slack=2.0),
                "B": scheduled("B", slack=5.0),
            },
            issues=(),
        )
        scores = [scored("A", total=80.0), scored("B", total=60.0)]
        return state, session, analysis, scores

    def test_result_model_views(self):
        state, session, analysis, scores = self._two_task_state()

        result = create_decision_result(session, state, analysis, scores)

        assert isinstance(result, DecisionResult)
        assert result.planning_period == WEEK
        assert result.candidate_count == 2
        assert result.ranked_tasks[0].rank == 1
        assert str(result.ranked_tasks[0]).startswith("#1 A")


class TestEndToEndWithRealScoring:
    def test_scoring_then_ranking_produces_the_expected_order(self):
        tasks = [
            make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      notes="trench work in city center", total_slack_days=0.0,
                      assigned_resource_ids=("R1",)),
            make_task("B", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      notes="office cleanup", total_slack_days=5.0,
                      assigned_resource_ids=("R1",)),
        ]
        state = make_state(tasks, resources=[Resource(id="R1", name="Alice")])
        session = create_planning_session(state, WEEK)
        analysis = analyze_schedule(state)
        scores = list(
            score_planning_session(
                session, analysis,
                consequence_of_delay={"A": 1.0},  # B stays provisional
                parameters=ScoringParameters(),
            )
        )

        result = create_decision_result(
            session, state, analysis, scores, analyze_resource_conflicts(state)
        )

        by_id = {ranked.task_id: ranked for ranked in result.ranked_tasks}
        # A: SP = 1.0 (slack 0), CD evaluated 1.0 → 40 + 40 = 80.
        # B: SP = 0.0 (slack = H = 5), CD provisional 0.5 → 0 + 20 = 20.
        assert rank_ids(result) == ["A", "B"]
        assert by_id["A"].consequence_source == "evaluated"
        assert by_id["B"].consequence_source == "provisional"
        assert by_id["A"].total_score > by_id["B"].total_score
        warnings = result.tasks_with_resource_warnings()
        assert [w.task_id for w in warnings] == ["A", "B"]  # rank callout order
