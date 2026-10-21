"""Tests for the project analysis indicators."""

from datetime import date, datetime

from project_decision.application.project_analysis import (
    ScheduleDelay,
    analyze_project_state,
)
from project_decision.domain import ProjectState, Resource, Task
from tests.conftest import make_task

CAPTURED_AT = datetime(2026, 10, 20, 9, 0)


def make_state(tasks, **date_overrides) -> ProjectState:
    dates = {
        "project_start": None,
        "project_finish": None,
        "status_date": None,
        "baseline_start": None,
        "baseline_finish": None,
    }
    dates.update(date_overrides)
    return ProjectState(
        project_id="P1",
        captured_at=CAPTURED_AT,
        project_name="Demo",
        tasks=list(tasks),
        **dates,
    )


class TestTaskCounts:
    def test_counts_by_progress(self):
        state = make_state(
            [
                make_task("T1", progress=1.0),   # completed
                make_task("T2", progress=1.0),   # completed
                make_task("T3", progress=0.4),   # in progress
                make_task("T4", progress=0.0),   # not started
            ]
        )
        analysis = analyze_project_state(state)

        assert analysis.total_tasks == 4
        assert analysis.completed_tasks == 2
        assert analysis.in_progress_tasks == 1
        assert analysis.not_started_tasks == 1

    def test_empty_project(self):
        analysis = analyze_project_state(make_state([]))

        assert analysis.total_tasks == 0
        assert analysis.completed_tasks == 0
        assert analysis.in_progress_tasks == 0
        assert analysis.not_started_tasks == 0
        assert analysis.tasks_with_missing_dates == 0
        assert analysis.overall_completion is None

    def test_tasks_with_missing_dates_are_counted(self):
        tasks = [
            make_task("T1", duration_days=1),                        # no dates at all
            make_task("T2", duration_days=1, start=date(2026, 1, 1)),  # partial (fine)
            make_task("T3", duration_days=1, start=date(2026, 1, 1), finish=date(2026, 1, 2)),
        ]
        analysis = analyze_project_state(make_state(tasks))

        assert analysis.tasks_with_missing_dates == 1

    def test_zero_progress_is_never_marked_overdue(self):
        # A not-started task with a past finish date is only counted as
        # not-started; there is no overdue indicator anywhere.
        overdue_looking = make_task("T1", progress=0.0, start=date(2020, 1, 1), finish=date(2020, 3, 1))
        analysis = analyze_project_state(make_state([overdue_looking]))

        assert analysis.not_started_tasks == 1


class TestPlannedDates:
    def test_planned_dates_come_from_the_state(self):
        state = make_state(
            [],
            project_start=date(2026, 1, 5),
            project_finish=date(2026, 9, 30),
            status_date=date(2026, 3, 1),
        )
        analysis = analyze_project_state(state)

        assert analysis.planned_start == date(2026, 1, 5)
        assert analysis.planned_finish == date(2026, 9, 30)
        assert analysis.status_date == date(2026, 3, 1)

    def test_missing_dates_stay_missing(self):
        analysis = analyze_project_state(make_state([]))

        assert analysis.planned_start is None
        assert analysis.planned_finish is None


class TestOverallCompletion:
    def test_duration_weighted_mean(self):
        tasks = [
            make_task("T1", duration_days=9.0, progress=0.0),
            make_task("T2", duration_days=1.0, progress=1.0),
        ]
        # (9*0.0 + 1*1.0) / 10 = 0.1
        assert analyze_project_state(make_state(tasks)).overall_completion == 0.1

    def test_zero_duration_tasks_cannot_produce_a_completion(self):
        tasks = [make_task("T1", duration_days=0.0, progress=0.0)]
        assert analyze_project_state(make_state(tasks)).overall_completion is None


class TestScheduleDelay:
    def test_without_baseline_delay_is_honestly_unavailable(self):
        analysis = analyze_project_state(
            make_state([], project_finish=date(2026, 9, 30))
        )

        delay = analysis.schedule_delay
        assert delay.available is False
        assert delay.delay_days is None
        assert "baseline" in delay.explanation.lower()

    def test_without_scheduled_finish_delay_is_unavailable(self):
        analysis = analyze_project_state(
            make_state([], baseline_finish=date(2026, 9, 1))
        )

        assert analysis.schedule_delay.available is False

    def test_later_finish_than_baseline_is_delay(self):
        analysis = analyze_project_state(
            make_state(
                [],
                project_finish=date(2026, 9, 10),
                baseline_finish=date(2026, 9, 1),
            )
        )

        delay = analysis.schedule_delay
        assert delay == ScheduleDelay(
            available=True, delay_days=9, explanation=delay.explanation
        )
        assert "9 day(s)" in delay.explanation

    def test_earlier_finish_than_baseline_is_no_delay(self):
        analysis = analyze_project_state(
            make_state(
                [],
                project_finish=date(2026, 8, 1),
                baseline_finish=date(2026, 9, 1),
            )
        )

        delay = analysis.schedule_delay
        assert delay.available is True
        assert delay.delay_days == 0

    def test_same_finish_as_baseline_is_no_delay(self):
        analysis = analyze_project_state(
            make_state(
                [],
                project_finish=date(2026, 9, 1),
                baseline_finish=date(2026, 9, 1),
            )
        )

        assert analysis.schedule_delay.delay_days == 0

    def test_delay_never_compares_with_today(self):
        # Finish in the future with a baseline in the future: still no
        # claim of delay, and no use of today's date anywhere.
        analysis = analyze_project_state(
            make_state(
                [],
                project_finish=date(2027, 6, 30),
                baseline_finish=date(2027, 6, 30),
            )
        )

        assert analysis.schedule_delay.delay_days == 0
