from datetime import date

import pytest

from project_decision.domain import Task
from tests.conftest import make_task


class TestTaskCreation:
    def test_valid_task_with_defaults(self):
        task = make_task()

        assert task.id == "T1"
        assert task.duration_days == 5.0
        assert task.progress == 0.0
        assert task.start is None
        assert task.finish is None
        assert task.dependency_ids == []
        assert task.assigned_resource_ids == []

    def test_valid_task_with_all_fields(self):
        task = Task(
            id="T2",
            name="Design review",
            duration_days=3.0,
            progress=0.5,
            start=date(2026, 5, 1),
            finish=date(2026, 5, 4),
            dependency_ids=["T1"],
            assigned_resource_ids=["R1"],
        )

        assert task.start == date(2026, 5, 1)
        assert task.finish == date(2026, 5, 4)
        assert task.dependency_ids == ["T1"]

    def test_dates_are_plain_calendar_dates(self):
        task = Task(id="T", name="x", duration_days=1, start=date(2026, 1, 1))
        assert task.start == date(2026, 1, 1)


class TestTaskValidation:
    def test_empty_id_rejected(self):
        with pytest.raises(ValueError, match="id"):
            Task(id="", name="x", duration_days=1)

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name"):
            Task(id="T", name="", duration_days=1)

    @pytest.mark.parametrize("duration", [-1.0, -0.001])
    def test_negative_duration_rejected(self, duration):
        with pytest.raises(ValueError, match="duration"):
            Task(id="T", name="x", duration_days=duration)

    @pytest.mark.parametrize("progress", [-0.1, 1.1, 2.0])
    def test_progress_out_of_range_rejected(self, progress):
        with pytest.raises(ValueError, match="progress"):
            Task(id="T", name="x", duration_days=1, progress=progress)

    def test_finish_before_start_rejected(self):
        with pytest.raises(ValueError, match="finish"):
            Task(
                id="T",
                name="x",
                duration_days=1,
                start=date(2026, 5, 4),
                finish=date(2026, 5, 1),
            )

    def test_progress_boundaries_accepted(self):
        assert make_task(progress=0.0).progress == 0.0
        assert make_task(progress=1.0).progress == 1.0
