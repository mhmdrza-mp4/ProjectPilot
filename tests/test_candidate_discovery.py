"""Tests for default candidate discovery (no MSP, deterministic)."""

from datetime import date, datetime

from project_decision.application.candidate_discovery import (
    discover_default_candidates,
)
from project_decision.domain import PlanningPeriod, ProjectState, Resource
from tests.conftest import make_task

CAPTURED_AT = datetime(2026, 10, 21, 9, 0)

PERIOD_START = date(2026, 6, 10)
PERIOD_END = date(2026, 6, 16)
PERIOD = PlanningPeriod(start_date=PERIOD_START, end_date=PERIOD_END)


def make_state(tasks) -> ProjectState:
    return ProjectState(
        project_id="P1",
        captured_at=CAPTURED_AT,
        project_name="Demo",
        tasks=list(tasks),
        resources=[Resource(id="R1", name="Alice")],
    )


def task(task_id, start, finish, **overrides):
    defaults = dict(start=start, finish=finish, assigned_resource_ids=["R1"])
    defaults.update(overrides)
    return make_task(task_id, **defaults)


class TestInclusiveOverlap:
    def test_incomplete_task_overlapping_the_period_is_included(self):
        state = make_state([task("A", PERIOD_START, date(2026, 6, 12), progress=0.5)])

        assert discover_default_candidates(state, PERIOD) == ("A",)

    def test_task_started_before_period_and_continuing_into_it_is_included(self):
        state = make_state(
            [task("A", date(2026, 6, 1), date(2026, 6, 11), progress=0.3)]
        )

        assert discover_default_candidates(state, PERIOD) == ("A",)

    def test_task_starting_during_period_and_finishing_after_it_is_included(self):
        state = make_state([task("A", date(2026, 6, 12), date(2026, 6, 25))])

        assert discover_default_candidates(state, PERIOD) == ("A",)

    def test_task_completely_before_the_period_is_excluded(self):
        state = make_state([task("A", date(2026, 6, 1), date(2026, 6, 9))])

        assert discover_default_candidates(state, PERIOD) == ()

    def test_task_completely_after_the_period_is_excluded(self):
        state = make_state([task("A", date(2026, 6, 17), date(2026, 6, 30))])

        assert discover_default_candidates(state, PERIOD) == ()

    def test_boundary_touching_days_count_as_overlap(self):
        before = make_state([task("A", PERIOD_START, PERIOD_END)])
        edge_case = make_state(
            [
                task("B", date(2026, 6, 9), PERIOD_START),  # touches first day
                task("C", PERIOD_END, date(2026, 6, 17)),   # touches last day
            ]
        )

        assert discover_default_candidates(before, PERIOD) == ("A",)
        assert discover_default_candidates(edge_case, PERIOD) == ("B", "C")


class TestDefaultRules:
    def test_completed_task_is_excluded(self):
        state = make_state([task("A", PERIOD_START, PERIOD_END, progress=1.0)])

        assert discover_default_candidates(state, PERIOD) == ()

    def test_summary_task_is_excluded(self):
        state = make_state(
            [make_task("S1", start=PERIOD_START, finish=PERIOD_END, is_summary=True)]
        )

        assert discover_default_candidates(state, PERIOD) == ()

    def test_missing_start_is_excluded_and_never_inferred(self):
        state = make_state([make_task("A", finish=PERIOD_END, start=None)])

        assert discover_default_candidates(state, PERIOD) == ()

    def test_missing_finish_is_excluded_and_never_inferred(self):
        state = make_state([make_task("A", start=PERIOD_START, finish=None)])

        assert discover_default_candidates(state, PERIOD) == ()

    def test_order_follows_the_state_task_order(self):
        state = make_state(
            [
                task("T2", PERIOD_START, PERIOD_END),
                task("T1", PERIOD_START, PERIOD_END),
                task("T3", PERIOD_START, PERIOD_END),
            ]
        )

        assert discover_default_candidates(state, PERIOD) == ("T2", "T1", "T3")

    def test_repeated_discovery_is_identical(self):
        state = make_state(
            [
                task("A", date(2026, 6, 8), date(2026, 6, 11)),
                task("B", PERIOD_START, date(2026, 6, 20)),
            ]
        )

        assert (
            discover_default_candidates(state, PERIOD)
            == discover_default_candidates(state, PERIOD)
        )
