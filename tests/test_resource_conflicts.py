"""Tests for resource-conflict analysis: no Microsoft Project, no COM, deterministic."""

from datetime import date, datetime
from types import SimpleNamespace

from project_decision.application.resource_conflicts import (
    ConflictStatus,
    analyze_resource_conflicts,
)
from project_decision.domain import ProjectState, Resource
from tests.conftest import make_task

CAPTURED_AT = datetime(2026, 10, 20, 9, 0)

JAN10 = date(2026, 1, 10)
JAN11 = date(2026, 1, 11)
JAN12 = date(2026, 1, 12)
JAN14 = date(2026, 1, 14)
JAN15 = date(2026, 1, 15)
JAN16 = date(2026, 1, 16)
JAN18 = date(2026, 1, 18)
JAN20 = date(2026, 1, 20)
JAN25 = date(2026, 1, 25)


def make_state(tasks, resources=()) -> ProjectState:
    return ProjectState(
        project_id="P1",
        captured_at=CAPTURED_AT,
        project_name="Demo",
        tasks=list(tasks),
        resources=list(resources),
    )


def scheduled(task_id, start, finish, resources):
    return make_task(task_id, start=start, finish=finish, assigned_resource_ids=resources)


class TestOverlapDetection:
    def test_two_tasks_share_one_resource_and_overlap(self):
        state = make_state(
            [
                scheduled("A", JAN10, JAN15, ["R1"]),
                scheduled("B", JAN12, JAN18, ["R1"]),
            ],
            resources=[Resource(id="R1", name="Alice")],
        )

        analysis = analyze_resource_conflicts(state)

        assert len(analysis.conflicts) == 1
        conflict = analysis.conflicts[0]
        assert conflict.status is ConflictStatus.CONFLICT
        assert conflict.task_ids == ("A", "B")
        assert conflict.resource_ids == ("R1",)
        assert conflict.resource_names == ("Alice",)
        assert conflict.overlap_start == JAN12
        assert conflict.overlap_finish == JAN15

    def test_shared_resource_without_overlap_gives_no_result(self):
        state = make_state(
            [
                scheduled("A", JAN10, JAN15, ["R1"]),
                scheduled("B", JAN20, JAN25, ["R1"]),
            ],
            resources=[Resource(id="R1", name="Alice")],
        )

        analysis = analyze_resource_conflicts(state)

        # Non-overlap is provable: honestly no conflict entry at all.
        assert analysis.conflicts == ()
        assert analysis.issues == ()

    def test_boundary_day_counts_as_overlap(self):
        # A ends Jan 15, B starts Jan 15 -> they share the Jan 15 day.
        state = make_state(
            [
                scheduled("A", JAN10, JAN15, ["R1"]),
                scheduled("B", JAN15, JAN20, ["R1"]),
            ],
            resources=[Resource(id="R1", name="Alice")],
        )

        analysis = analyze_resource_conflicts(state)

        conflict = analysis.conflicts[0]
        assert conflict.status is ConflictStatus.CONFLICT
        assert conflict.overlap_start == JAN15
        assert conflict.overlap_finish == JAN15

    def test_multiple_shared_resources_single_pair_entry(self):
        state = make_state(
            [
                scheduled("A", JAN10, JAN15, ["R2", "R1"]),
                scheduled("B", JAN11, JAN14, ["R1", "R2"]),
            ],
            resources=[
                Resource(id="R1", name="Alice"),
                Resource(id="R2", name="Excavator"),
            ],
        )

        analysis = analyze_resource_conflicts(state)

        # One conflict for the pair, not one per shared resource.
        assert len(analysis.conflicts) == 1
        conflict = analysis.conflicts[0]
        assert conflict.resource_ids == ("R1", "R2")  # state resource order
        assert conflict.resource_names == ("Alice", "Excavator")

    def test_tasks_without_resources_never_conflict(self):
        state = make_state(
            [
                scheduled("A", JAN10, JAN15, []),
                scheduled("B", JAN10, JAN15, []),
            ],
            resources=[Resource(id="R1", name="Alice")],
        )

        analysis = analyze_resource_conflicts(state)

        assert analysis.conflicts == ()
        assert analysis.issues == ()  # no assignments is normal, not a problem

    def test_single_day_tasks_share_their_one_day(self):
        state = make_state(
            [
                scheduled("A", JAN15, JAN15, ["R1"]),
                scheduled("B", JAN15, JAN15, ["R1"]),
            ],
            resources=[Resource(id="R1", name="Alice")],
        )

        analysis = analyze_resource_conflicts(state)

        conflict = analysis.conflicts[0]
        assert conflict.overlap_start == JAN15 == conflict.overlap_finish


class TestMissingData:
    def test_missing_start_date_is_unknown_not_a_conflict(self):
        state = make_state(
            [
                make_task("A", finish=JAN15, assigned_resource_ids=["R1"]),
                scheduled("B", JAN10, JAN20, ["R1"]),
            ],
            resources=[Resource(id="R1", name="Alice")],
        )

        analysis = analyze_resource_conflicts(state)

        conflict = analysis.conflicts[0]
        assert conflict.status is ConflictStatus.UNKNOWN
        assert conflict.overlap_start is None
        assert conflict.overlap_finish is None
        assert "task 'A' has no start date" in conflict.explanation

    def test_missing_finish_date_is_unknown_not_a_conflict(self):
        state = make_state(
            [
                scheduled("A", JAN10, JAN20, ["R1"]),
                make_task("B", start=JAN10, assigned_resource_ids=["R1"]),
            ],
            resources=[Resource(id="R1", name="Alice")],
        )

        analysis = analyze_resource_conflicts(state)

        conflict = analysis.conflicts[0]
        assert conflict.status is ConflictStatus.UNKNOWN
        assert "task 'B' has no finish date" in conflict.explanation
        assert "task 'A'" not in conflict.explanation

    def test_both_dates_missing_lists_every_missing_value(self):
        state = make_state(
            [
                make_task("A", assigned_resource_ids=["R1"]),
                make_task("B", assigned_resource_ids=["R1"]),
            ],
            resources=[Resource(id="R1", name="Alice")],
        )

        analysis = analyze_resource_conflicts(state)

        conflict = analysis.conflicts[0]
        assert conflict.status is ConflictStatus.UNKNOWN
        assert "task 'A' has no start date" in conflict.explanation
        assert "task 'A' has no finish date" in conflict.explanation
        assert "task 'B' has no start date" in conflict.explanation
        assert "task 'B' has no finish date" in conflict.explanation

    def test_unknown_resource_id_is_an_issue_and_creates_no_conflict(self):
        state = make_state(
            [
                scheduled("A", JAN10, JAN15, ["R9"]),
                scheduled("B", JAN10, JAN15, ["R9"]),
            ],
            resources=[Resource(id="R1", name="Alice")],
        )

        analysis = analyze_resource_conflicts(state)

        # Both reference the same (nonexistent) resource: not a conflict.
        assert analysis.conflicts == ()
        assert any(
            "task 'A' references unknown resource 'R9'" in issue
            for issue in analysis.issues
        )
        assert any("task 'B'" in issue for issue in analysis.issues)

    def test_resource_with_unusable_capacity_is_an_issue_but_still_shared(self):
        # The domain validates capacity >= 0 at construction; snapshots
        # bypassing it are handled defensively here.
        broken_resource = SimpleNamespace(id="R1", name="Alice", capacity_units=None)
        state = make_state(
            [
                scheduled("A", JAN10, JAN15, ["R1"]),
                scheduled("B", JAN12, JAN14, ["R1"]),
            ],
            resources=[broken_resource],
        )

        analysis = analyze_resource_conflicts(state)

        conflict = analysis.conflicts[0]
        assert conflict.status is ConflictStatus.CONFLICT
        assert any("unusable capacity" in issue for issue in analysis.issues)


class TestMultipleTasks:
    def test_three_tasks_on_one_resource_produce_deterministic_pairs(self):
        state = make_state(
            [
                scheduled("A", JAN10, JAN15, ["R1"]),   # overlaps B, D
                scheduled("B", JAN12, JAN20, ["R1"]),   # overlaps A, C, D
                scheduled("C", JAN16, JAN25, ["R1"]),   # overlaps B, D
                scheduled("D", JAN10, JAN15, ["R1"]),
            ],
            resources=[Resource(id="R1", name="Alice")],
        )

        analysis = analyze_resource_conflicts(state)

        # C is disjoint from A and D; omitted provably.
        assert [pair.task_ids for pair in analysis.conflicts] == [
            ("A", "B"),
            ("A", "D"),
            ("B", "C"),
            ("B", "D"),
        ]

    def test_pair_order_follows_state_order_not_ids(self):
        state = make_state(
            [
                scheduled("B", JAN12, JAN18, ["R1"]),  # listed first
                scheduled("A", JAN10, JAN14, ["R1"]),
            ],
            resources=[Resource(id="R1", name="Alice")],
        )

        analysis = analyze_resource_conflicts(state)

        assert [pair.task_ids for pair in analysis.conflicts] == [("B", "A")]
        assert analysis.conflicts[0].overlap_start == JAN12
        assert analysis.conflicts[0].overlap_finish == JAN14

    def test_disjoint_pair_omitted_between_conflicts_in_state_order(self):
        state = make_state(
            [
                scheduled("B", JAN20, JAN25, ["R1"]),  # first listed, overlaps nothing
                scheduled("A", JAN10, JAN15, ["R1"]),  # overlaps C only
                scheduled("C", JAN10, JAN15, ["R1"]),
            ],
            resources=[Resource(id="R1", name="Alice")],
        )

        analysis = analyze_resource_conflicts(state)

        # The provably non-overlapping pair is simply absent.
        assert [pair.task_ids for pair in analysis.conflicts] == [("A", "C")]

    def test_repeated_analysis_of_the_same_state_is_identical(self):
        state = make_state(
            [
                scheduled("A", JAN10, JAN15, ["R1", "R2"]),
                scheduled("B", JAN11, JAN14, ["R2"]),
                scheduled("C", JAN12, JAN18, ["R1"]),
            ],
            resources=[Resource(id="R1", name="Alice"), Resource(id="R2", name="Bob")],
        )

        first = analyze_resource_conflicts(state)
        second = analyze_resource_conflicts(state)

        assert first == second


class TestDuplicateAssignments:
    def test_duplicate_resource_id_on_task_collapses_and_is_reported(self):
        state = make_state(
            [
                scheduled("A", JAN10, JAN15, ["R1", "R1"]),
                scheduled("B", JAN12, JAN14, ["R1"]),
            ],
            resources=[Resource(id="R1", name="Alice")],
        )

        analysis = analyze_resource_conflicts(state)

        assert len(analysis.conflicts) == 1  # R1 shared once, not twice
        assert any(
            "more than once" in issue and "'A'" in issue for issue in analysis.issues
        )
