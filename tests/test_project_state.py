"""Tests for extended ProjectState fields and validation."""

from datetime import date, datetime

import pytest

from project_decision.domain import ProjectState
from tests.conftest import make_project

CAPTURED_AT = datetime(2026, 10, 20, 9, 0)


class TestProjectStateCreation:
    def test_minimal_state(self):
        state = ProjectState(project_id="P1", captured_at=CAPTURED_AT)

        assert state.project_id == "P1"
        assert state.project_name == ""
        assert state.tasks == []
        # All schedule/baseline information explicitly unavailable.
        assert state.project_start is None
        assert state.project_finish is None
        assert state.status_date is None
        assert state.baseline_start is None
        assert state.baseline_finish is None
        assert state.capture_warnings == ()

    def test_capture_warnings_survive_as_diagnostics(self):
        state = ProjectState(
            project_id="P1",
            captured_at=CAPTURED_AT,
            capture_warnings=("1 predecessor reference omitted",),
        )

        assert state.capture_warnings == ("1 predecessor reference omitted",)

    def test_full_state(self):
        state = ProjectState(
            project_id="P1",
            captured_at=CAPTURED_AT,
            project_name="Bridge repair",
            project_start=date(2026, 1, 5),
            project_finish=date(2026, 9, 30),
            status_date=date(2026, 3, 1),
            baseline_start=date(2026, 1, 5),
            baseline_finish=date(2026, 9, 1),
        )

        assert state.project_name == "Bridge repair"
        assert state.baseline_finish == date(2026, 9, 1)

    def test_empty_project_id_rejected(self):
        with pytest.raises(ValueError, match="project_id"):
            ProjectState(project_id="", captured_at=CAPTURED_AT)

    def test_none_capture_time_rejected(self):
        with pytest.raises(ValueError, match="captured_at"):
            ProjectState(project_id="P", captured_at=None)  # type: ignore[arg-type]


class TestFromProjectCopiesScheduleContext:
    def test_schedule_and_baseline_dates_are_snapshotted(self):
        project = make_project(
            project_start=date(2026, 1, 5),
            project_finish=date(2026, 9, 30),
            status_date=date(2026, 3, 1),
            baseline_start=date(2026, 1, 5),
            baseline_finish=date(2026, 9, 1),
        )

        state = project.capture_state(captured_at=CAPTURED_AT)

        assert state.project_name == "Demo Project"
        assert state.project_start == project.project_start
        assert state.project_finish == project.project_finish
        assert state.status_date == project.status_date
        assert state.baseline_start == project.baseline_start
        assert state.baseline_finish == project.baseline_finish

    def test_project_without_dates_snapshots_none(self):
        state = make_project().capture_state(captured_at=CAPTURED_AT)

        assert state.project_start is None
        assert state.baseline_finish is None
