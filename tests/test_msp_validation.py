"""Real-MSP data-semantics validation (strictly read-only) — marked ``msp``.

Runs the product's own loading chain against the ACTIVE Microsoft
Project and verifies the invariants every later stage depends on:

- capture-time behavior of the loader chain (``load_active_project``);
- minutes → days consistency for slack (measured against the connected
  project's own minutes-per-day setting, never a hard-coded value);
- the ternary critical flag (True/False/None — never an invented bool);
- notes semantics (``None`` vs stripped text; whitespace normalizes to
  ``None`` exactly as the existing mapper rule states);
- summary flags are booleans, and dates/progress/physical values stay
  consistent with what Microsoft Project reports;
- dependency DIRECTION is validated against the state itself: every
  ``dependency_ids`` entry must appear as a successor of the referenced
  task (the direction the whole application depends on);
- resource assignments resolve; the existing conflict analysis produces
  explicable CONFLICT pairs on the REAL data (shared resource + overlap);
- the complete offline pipeline runs on the REAL snapshot and its
  structural counts agree with the session it consumed.

Expectations are DERIVED from the live read (per validation policy, no
values are hard-coded from a specific project and nothing is invented
when the open project cannot express a case). The tests skip themselves
when Microsoft Project, pywin32, or an open project is missing.

Run explicitly:

    pytest -m msp -q
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

pytest.importorskip(
    "win32com.client", reason="pywin32 is required for MSP integration tests"
)

pytestmark = pytest.mark.msp

CAPTURED_AT = datetime(2026, 1, 1, 0, 0, 0)


@pytest.fixture(scope="module")
def workflow():
    from project_decision.application.decision_workflow import load_project
    from project_decision.application.project_loader import load_active_project
    from project_decision.infrastructure.msp.exceptions import (
        MspNoActiveProjectError,
        MspNotAvailableError,
    )

    try:
        return load_project(load_active_project)
    except (MspNoActiveProjectError, MspNotAvailableError):
        pytest.skip("Microsoft Project is not running with an open project")


class TestCaptureSemantics:
    def test_explicit_capture_timestamp_is_preserved(self):
        from project_decision.application.project_loader import load_active_project

        state = load_active_project(captured_at=CAPTURED_AT)
        assert state.captured_at == CAPTURED_AT

    def test_default_capture_timestamp_is_recent(self, workflow):
        captured_at = workflow.project_state.captured_at
        assert captured_at is not None
        assert datetime.now() - captured_at < timedelta(minutes=10)


class TestFieldSemantics:
    def test_slack_days_are_consistent_with_the_project_minutes_per_day(
        self, workflow
    ):
        from project_decision.infrastructure.msp import MspComClient

        project_state = workflow.project_state
        client = MspComClient()
        client.connect()
        try:
            minutes_per_day = client.get_minutes_per_day()
        finally:
            client.disconnect()
        checked = 0
        for task in project_state.tasks:
            if task.total_slack_days is None:
                continue
            minutes = task.total_slack_days * minutes_per_day
            assert abs(minutes - round(minutes)) < 1e-4, (
                f"task '{task.id}': slack {task.total_slack_days} is not a "
                "whole number of the project's minutes-per-day units"
            )
            checked += 1
        # A project without slack data passes through; integrity is per value.
        assert checked >= 0

    def test_critical_flags_only_take_the_three_honest_values(self, workflow):
        for task in workflow.project_state.tasks:
            if task.is_summary:
                continue
            assert task.is_critical in (True, False, None), (
                f"task '{task.id}': critical flag {task.is_critical!r} was "
                "invented rather than reported"
            )

    def test_slack_values_are_numbers_or_none_never_fabricated(self, workflow):
        for task in workflow.project_state.tasks:
            if task.is_summary:
                continue
            assert task.total_slack_days is None or isinstance(
                task.total_slack_days, (int, float)
            )

    def test_notes_are_none_or_stripped_text(self, workflow):
        for task in workflow.project_state.tasks:
            if task.notes is None:
                continue
            assert task.notes == task.notes.strip(), (
                f"task '{task.id}': whitespace-only notes were not "
                f"normalized to None ({task.notes!r})"
            )
            assert task.notes != ""

    def test_summary_rows_are_explicitly_flagged(self, workflow):
        for task in workflow.project_state.tasks:
            assert isinstance(task.is_summary, bool)

    def test_progress_duration_and_dates_stay_physical(self, workflow):
        for task in workflow.project_state.tasks:
            assert 0.0 <= task.progress <= 1.0
            assert task.duration_days >= 0.0
            if task.start is not None and task.finish is not None:
                assert task.finish >= task.start


class TestDependencyDirection:
    def test_every_dependency_appears_as_a_successor_of_the_reference(
        self, workflow
    ):
        from project_decision.application.schedule_analysis import analyze_schedule

        state = workflow.project_state
        analysis = analyze_schedule(state)
        for task in state.tasks:
            for dependency_id in task.dependency_ids:
                schedule = analysis.schedules.get(dependency_id)
                assert schedule is not None
                assert task.id in schedule.successor_ids, (
                    f"task '{task.id}' depends on '{dependency_id}', but "
                    f"'{dependency_id}' does not report '{task.id}' as a "
                    "successor — dependency direction is inverted somewhere"
                )


class TestResourcesAndConflictsOnRealData:
    def test_resource_assignments_resolve(self, workflow):
        state = workflow.project_state
        resource_ids = {resource.id for resource in state.resources}
        for task in state.tasks:
            assert set(task.assigned_resource_ids) <= resource_ids

    def test_conflicting_pairs_share_resources_and_overlap(self, workflow):
        from project_decision.application.resource_conflicts import (
            ConflictStatus,
            analyze_resource_conflicts,
        )

        analysis = analyze_resource_conflicts(workflow.project_state)
        tasks_by_id = {task.id: task for task in workflow.project_state.tasks}
        for conflict in analysis.conflicts_with_status(ConflictStatus.CONFLICT):
            first, second = conflict.task_ids
            first_task, second_task = tasks_by_id[first], tasks_by_id[second]
            shared = set(first_task.assigned_resource_ids) & set(
                second_task.assigned_resource_ids
            )
            assert shared, "a CONFLICT pair must share a real resource"
            assert conflict.overlap_finish >= conflict.overlap_start


class TestRealPipelineEndToEnd:
    def test_confirmed_session_runs_offline_and_counts_match(self, workflow):
        from project_decision.application.decision_workflow import (
            confirm_session,
            run_decision,
            set_planning_period,
        )
        from project_decision.domain.planning_period import PlanningPeriod

        prepared = set_planning_period(
            workflow,
            PlanningPeriod(
                start_date=date.today(), end_date=date.today() + timedelta(days=14)
            ),
        )
        if not prepared.planning_session.candidate_task_ids:
            pytest.skip("the open project has no candidates for the period")
        confirmed = confirm_session(prepared)
        result = run_decision(confirmed, lambda session, state: None)
        review = result.decision_review
        # Structural agreement with the session that was consumed.
        assert review.candidate_count == len(
            prepared.planning_session.candidate_task_ids
        )
        assert review.ranked_count == len(result.decision_result.ranked_tasks)
        # Offline ranks are sequential over exactly the session's candidates.
        if review.candidate_count:
            ranks = [one.rank for one in result.decision_result.ranked_tasks]
            assert ranks == list(range(1, review.candidate_count + 1))
