"""Tests for objective task status classification rules."""

import pytest

from project_decision.domain import TaskStatus, classify_task
from tests.conftest import make_task


class TestTaskClassificationRules:
    def test_zero_progress_is_not_started(self):
        assert classify_task(make_task(progress=0.0)) is TaskStatus.NOT_STARTED

    def test_partial_progress_is_in_progress(self):
        assert classify_task(make_task(progress=0.01)) is TaskStatus.IN_PROGRESS
        assert classify_task(make_task(progress=0.5)) is TaskStatus.IN_PROGRESS
        assert classify_task(make_task(progress=0.99)) is TaskStatus.IN_PROGRESS

    def test_full_progress_is_completed(self):
        assert classify_task(make_task(progress=1.0)) is TaskStatus.COMPLETED

    def test_classification_does_not_use_dates(self):
        # A not-started task with an old finish date is still NOT_STARTED:
        # lateness is never inferred from progress or dates here.
        from datetime import date

        task = make_task(progress=0.0)
        task.finish = date(2000, 1, 1)
        assert classify_task(task) is TaskStatus.NOT_STARTED


class TestTaskStatusLabels:
    @pytest.mark.parametrize(
        ("status", "label"),
        [
            (TaskStatus.COMPLETED, "Completed"),
            (TaskStatus.IN_PROGRESS, "In progress"),
            (TaskStatus.NOT_STARTED, "Not started"),
        ],
    )
    def test_labels(self, status, label):
        from project_decision.domain import task_status_label

        assert task_status_label(status) == label


class TestInvalidDataCannotBeClassifiedSilently:
    def test_invalid_progress_is_rejected_by_the_domain(self):
        with pytest.raises(ValueError, match="progress"):
            make_task(progress=1.5)
