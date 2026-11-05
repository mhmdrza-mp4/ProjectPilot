"""Tests for schedule analysis: no Microsoft Project, no COM, deterministic."""

from datetime import datetime

from project_decision.application.schedule_analysis import (
    ScheduleAnalysis,
    analyze_schedule,
)
from project_decision.domain import ProjectState, Task
from tests.conftest import make_task

CAPTURED_AT = datetime(2026, 10, 20, 9, 0)


def make_state(tasks) -> ProjectState:
    return ProjectState(
        project_id="P1",
        captured_at=CAPTURED_AT,
        project_name="Demo",
        tasks=list(tasks),
    )


class TestSuccessorCalculation:
    def test_one_predecessor_yields_one_successor(self):
        state = make_state(
            [
                make_task("A"),
                make_task("B", dependency_ids=["A"]),
            ]
        )

        analysis = analyze_schedule(state)

        a = analysis.schedules["A"]
        assert a.successor_ids == ("B",)
        assert a.successor_count == 1
        assert analysis.schedules["B"].successor_ids == ()

    def test_one_task_with_multiple_successors(self):
        state = make_state(
            [
                make_task("A"),
                make_task("B", dependency_ids=["A"]),
                make_task("C", dependency_ids=["A"]),
            ]
        )

        analysis = analyze_schedule(state)

        # Successor order is the order the tasks appear in the state,
        # so the result is deterministic.
        assert analysis.schedules["A"].successor_ids == ("B", "C")
        assert analysis.schedules["A"].successor_count == 2

    def test_task_with_multiple_predecessors(self):
        state = make_state(
            [
                make_task("A"),
                make_task("B"),
                make_task("C", dependency_ids=["A", "B"]),
            ]
        )

        analysis = analyze_schedule(state)

        assert analysis.schedules["A"].successor_ids == ("C",)
        assert analysis.schedules["B"].successor_ids == ("C",)
        assert analysis.schedules["C"].successor_ids == ()

    def test_task_with_no_successors(self):
        state = make_state(
            [
                make_task("A"),
                make_task("B"),
                make_task("C", dependency_ids=["B"]),
            ]
        )

        analysis = analyze_schedule(state)

        a = analysis.schedules["A"]
        assert a.successor_ids == ()
        assert a.successor_count == 0

    def test_empty_project(self):
        analysis = analyze_schedule(make_state([]))

        assert analysis.schedules == {}
        assert analysis.issues == ()
        assert isinstance(analysis, ScheduleAnalysis)

    def test_duplicated_dependency_ids_collapse_to_one_successor(self):
        state = make_state(
            [
                make_task("A"),
                make_task("B", dependency_ids=["A", "A"]),
            ]
        )

        analysis = analyze_schedule(state)

        assert analysis.schedules["A"].successor_ids == ("B",)
        assert analysis.schedules["A"].successor_count == 1
        assert any(
            "more than once" in issue and "'B'" in issue for issue in analysis.issues
        )

    def test_unknown_dependency_id_becomes_an_issue_not_a_task(self):
        state = make_state(
            [
                make_task("A"),
                make_task("B", dependency_ids=["A", "NOPE"]),
            ]
        )

        analysis = analyze_schedule(state)

        # No fake tasks are created; only the real relationship remains.
        assert analysis.schedules["A"].successor_ids == ("B",)
        assert "NOPE" not in analysis.schedules
        assert analysis.schedules["B"].successor_ids == ()
        assert any(
            "unknown task 'NOPE'" in issue for issue in analysis.issues
        )

    def test_self_reference_becomes_an_issue_not_a_successor(self):
        state = make_state([make_task("A", dependency_ids=["A"])])

        analysis = analyze_schedule(state)

        assert analysis.schedules["A"].successor_ids == ()
        assert analysis.schedules["A"].successor_count == 0
        assert any("depends on itself" in issue for issue in analysis.issues)

    def test_issue_order_follows_the_state_task_order(self):
        state = make_state(
            [
                make_task("B", dependency_ids=["NOPE-1"]),
                make_task("A", dependency_ids=["NOPE-2"]),
            ]
        )

        analysis = analyze_schedule(state)

        assert analysis.issues == (
            "task 'B' depends on unknown task 'NOPE-1'",
            "task 'A' depends on unknown task 'NOPE-2'",
        )

    def test_clean_chain_has_no_issues(self):
        state = make_state(
            [
                make_task("A"),
                make_task("B", dependency_ids=["A"]),
                make_task("C", dependency_ids=["B", "A"]),
            ]
        )

        analysis = analyze_schedule(state)

        assert analysis.issues == ()
        assert analysis.schedules["A"].successor_ids == ("B", "C")
        assert analysis.schedules["C"].successor_count == 0


class TestDownstreamCount:
    """Direct successors vs everything that transitively waits."""

    def test_chain_counts_indirect_dependents(self):
        state = make_state(
            [
                make_task("A"),
                make_task("B", dependency_ids=["A"]),
                make_task("C", dependency_ids=["B"]),
                make_task("D", dependency_ids=["C"]),
            ]
        )

        analysis = analyze_schedule(state)

        # A has ONE direct successor but three tasks wait behind it.
        assert analysis.schedules["A"].successor_count == 1
        assert analysis.schedules["A"].downstream_count == 3
        assert analysis.schedules["B"].downstream_count == 2
        assert analysis.schedules["C"].downstream_count == 1
        assert analysis.schedules["D"].downstream_count == 0

    def test_diamond_counts_each_task_once(self):
        state = make_state(
            [
                make_task("A"),
                make_task("B", dependency_ids=["A"]),
                make_task("C", dependency_ids=["A"]),
                make_task("D", dependency_ids=["B", "C"]),
            ]
        )

        analysis = analyze_schedule(state)

        assert analysis.schedules["A"].successor_count == 2
        assert analysis.schedules["A"].downstream_count == 3  # B, C, D (D once)

    def test_shared_tail_is_counted_once_per_start(self):
        state = make_state(
            [
                make_task("A"),
                make_task("B"),
                make_task("C", dependency_ids=["A", "B"]),
                make_task("D", dependency_ids=["C"]),
            ]
        )

        analysis = analyze_schedule(state)

        assert analysis.schedules["A"].downstream_count == 2
        assert analysis.schedules["B"].downstream_count == 2

    def test_task_without_successors_is_zero_not_missing(self):
        analysis = analyze_schedule(make_state([make_task("A")]))

        assert analysis.schedules["A"].downstream_count == 0

    def test_broken_references_do_not_create_downstream_tasks(self):
        state = make_state(
            [make_task("A"), make_task("B", dependency_ids=["A", "NOPE"])]
        )

        analysis = analyze_schedule(state)

        assert analysis.schedules["A"].downstream_count == 1
        assert "NOPE" not in analysis.schedules

    def test_long_chain_does_not_hit_the_recursion_limit(self):
        count = 3000
        tasks = [make_task("T0")] + [
            make_task(f"T{i}", dependency_ids=[f"T{i - 1}"]) for i in range(1, count)
        ]

        analysis = analyze_schedule(make_state(tasks))

        assert analysis.schedules["T0"].downstream_count == count - 1
        assert analysis.schedules[f"T{count - 1}"].downstream_count == 0

    def test_hand_built_schedule_defaults_to_the_direct_count(self):
        from project_decision.application.schedule_analysis import TaskSchedule

        schedule = TaskSchedule(
            task_id="A",
            successor_ids=("B", "C"),
            successor_count=2,
            total_slack_days=0.0,
            is_critical=None,
        )

        assert schedule.downstream_count == 2  # documented lower bound


class TestDependencyCycles:
    def test_two_task_cycle_terminates_and_is_reported(self):
        state = make_state(
            [
                make_task("A", dependency_ids=["B"]),
                make_task("B", dependency_ids=["A"]),
            ]
        )

        analysis = analyze_schedule(state)

        # Each is downstream of the other, never of itself.
        assert analysis.schedules["A"].downstream_count == 1
        assert analysis.schedules["B"].downstream_count == 1
        assert "task 'A' is part of a dependency cycle" in analysis.issues
        assert "task 'B' is part of a dependency cycle" in analysis.issues

    def test_only_tasks_on_the_cycle_are_reported(self):
        state = make_state(
            [
                make_task("START"),
                make_task("A", dependency_ids=["START", "B"]),
                make_task("B", dependency_ids=["A"]),
                make_task("AFTER", dependency_ids=["B"]),
            ]
        )

        analysis = analyze_schedule(state)

        cycle_issues = [i for i in analysis.issues if "cycle" in i]
        assert cycle_issues == [
            "task 'A' is part of a dependency cycle",
            "task 'B' is part of a dependency cycle",
        ]
        # START feeds the cycle and everything behind it.
        assert analysis.schedules["START"].downstream_count == 3

    def test_acyclic_state_reports_no_cycle(self):
        state = make_state([make_task("A"), make_task("B", dependency_ids=["A"])])

        assert not any("cycle" in i for i in analyze_schedule(state).issues)

    def test_self_reference_is_not_a_cycle(self):
        analysis = analyze_schedule(make_state([make_task("A", dependency_ids=["A"])]))

        assert analysis.schedules["A"].downstream_count == 0
        assert not any("cycle" in i for i in analysis.issues)


class TestScheduleFields:
    def test_normal_task_reports_slack_and_criticality_verbatim(self):
        state = make_state(
            [make_task("A", total_slack_days=2.5, is_critical=False)]
        )

        schedule = analyze_schedule(state).schedules["A"]

        assert schedule.total_slack_days == 2.5
        assert schedule.is_critical is False

    def test_zero_slack_is_reported_as_real_zero(self):
        # Zero slack is meaningful (critical path); absence is None.
        state = make_state([make_task("A", total_slack_days=0.0)])

        schedule = analyze_schedule(state).schedules["A"]

        assert schedule.total_slack_days == 0.0
        assert schedule.total_slack_days is not None

    def test_negative_slack_is_not_clamped(self):
        state = make_state([make_task("A", total_slack_days=-1.5)])

        schedule = analyze_schedule(state).schedules["A"]

        assert schedule.total_slack_days == -1.5

    def test_missing_slack_and_criticality_stay_none(self):
        state = make_state([make_task("A")])

        schedule = analyze_schedule(state).schedules["A"]

        assert schedule.total_slack_days is None
        assert schedule.is_critical is None
