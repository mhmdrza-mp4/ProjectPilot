"""Tests for planning-session creation and add/remove (state never mutated)."""

from copy import deepcopy
from datetime import date, datetime

import pytest

from project_decision.application.planning_session import (
    add_candidate,
    create_planning_session,
    remove_candidate,
)
from project_decision.domain import PlanningPeriod, ProjectState, Resource
from tests.conftest import make_task

CAPTURED_AT = datetime(2026, 10, 21, 9, 0)
PERIOD = PlanningPeriod(start_date=date(2026, 6, 10), end_date=date(2026, 6, 16))


def make_state() -> ProjectState:
    return ProjectState(
        project_id="P1",
        captured_at=CAPTURED_AT,
        project_name="Demo",
        tasks=[
            make_task("A", start=date(2026, 6, 10), finish=date(2026, 6, 12)),
            make_task("B", start=date(2026, 6, 11), finish=date(2026, 6, 14)),
            # C: outside the period -> NOT a default candidate.
            make_task("C", start=date(2026, 7, 1), finish=date(2026, 7, 5)),
            # D: summary row overlapping the period -> NOT a default candidate.
            make_task("D", start=date(2026, 6, 9), finish=date(2026, 6, 20), is_summary=True),
        ],
        resources=[Resource(id="R1", name="Alice")],
    )


class TestCreateFromDefaults:
    def test_create_uses_default_candidates(self):
        session = create_planning_session(make_state(), PERIOD)

        assert session.planning_period is PERIOD
        assert session.candidate_task_ids == ("A", "B")

    def test_create_with_explicit_ids_keeps_them_verbatim(self):
        session = create_planning_session(
            make_state(), PERIOD, candidate_task_ids=("C", "A")
        )

        # Explicit selection is not filtered or reordered: the manager
        # decides — even ids the rules would not have suggested.
        assert session.candidate_task_ids == ("C", "A")

    def test_empty_period_state_gives_empty_session(self):
        empty = ProjectState(project_id="P1", captured_at=CAPTURED_AT, project_name="")
        session = create_planning_session(empty, PERIOD)

        assert session.candidate_task_ids == ()

    def test_create_rejects_unknown_task_id(self):
        with pytest.raises(ValueError, match="unknown task id: NOPE"):
            create_planning_session(
                make_state(), PERIOD, candidate_task_ids=("A", "NOPE")
            )

    def test_create_rejects_duplicate_ids(self):
        with pytest.raises(ValueError, match="unique"):
            create_planning_session(
                make_state(), PERIOD, candidate_task_ids=("A", "A")
            )


class TestAddCandidate:
    def test_add_manually_includes_a_task_outside_default_rules(self):
        session = create_planning_session(make_state(), PERIOD)

        extended = add_candidate(make_state(), session, "C")

        # Explicit manual addition works although C lies outside the period.
        assert extended.candidate_task_ids == ("A", "B", "C")
        # The original session is immutable.
        assert session.candidate_task_ids == ("A", "B")

    def test_add_unknown_task_id_is_rejected(self):
        session = create_planning_session(make_state(), PERIOD)

        with pytest.raises(ValueError, match="unknown task id: NOPE"):
            add_candidate(make_state(), session, "NOPE")

    def test_add_duplicate_candidate_is_rejected(self):
        state = make_state()
        session = add_candidate(state, create_planning_session(state, PERIOD), "C")

        with pytest.raises(ValueError, match="already a candidate"):
            add_candidate(state, session, "C")


class TestRemoveCandidate:
    def test_remove_returns_new_session_without_the_task(self):
        state = make_state()
        session = create_planning_session(state, PERIOD)

        reduced = remove_candidate(session, "A")

        assert reduced.candidate_task_ids == ("B",)
        assert session.candidate_task_ids == ("A", "B")  # original untouched

    def test_remove_can_leave_an_empty_session(self):
        session = create_planning_session(make_state(), PERIOD)
        reduced = remove_candidate(session, "A")

        assert remove_candidate(reduced, "B").candidate_task_ids == ()

    def test_remove_non_candidate_is_rejected(self):
        session = create_planning_session(make_state(), PERIOD)

        with pytest.raises(ValueError, match="not a candidate"):
            remove_candidate(session, "C")


class TestStateIsNeverMutated:
    def test_discovery_and_session_operations_leave_state_untouched(self):
        original = make_state()
        pristine = deepcopy(original)

        session = create_planning_session(original, PERIOD)
        session = add_candidate(original, session, "C")
        session = remove_candidate(session, "A")

        assert original == pristine
        # The old sessions did not mutate either.
        assert create_planning_session(original, PERIOD).candidate_task_ids == ("A", "B")
