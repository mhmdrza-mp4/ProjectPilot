"""Unit tests for the MSP mapper: no Microsoft Project, no Windows, no COM."""

from datetime import date, datetime

import pytest

from project_decision.domain.constraint import ConstraintKind
from project_decision.infrastructure.msp.exceptions import MspMappingError
from project_decision.infrastructure.msp import msp_mapper as mapper
from tests.fakes.fake_msp import FakeMspDependency, FakeMspResource, FakeMspTask, Y2026


class TestConversions:
    def test_to_date_none_maps_to_none(self):
        assert mapper.to_date(None) is None

    def test_to_date_from_pywintypes_like_datetime(self):
        # pywintypes datetime is tz-aware and has a .date() method.
        assert mapper.to_date(Y2026) == date(2026, 6, 1)

    def test_to_date_accepts_plain_date(self):
        assert mapper.to_date(date(2026, 5, 4)) == date(2026, 5, 4)

    def test_to_date_rejects_uninterpretable_value(self):
        with pytest.raises(MspMappingError):
            mapper.to_date("NA")

    def test_duration_minutes_to_days(self):
        assert mapper.duration_minutes_to_days(480.0) == 1.0
        assert mapper.duration_minutes_to_days(240.0) == 0.5

    def test_duration_none_maps_to_zero(self):
        assert mapper.duration_minutes_to_days(None) == 0.0

    def test_percent_complete_fraction(self):
        assert mapper.percent_complete_to_fraction(50) == 0.5
        assert mapper.percent_complete_to_fraction(0) == 0.0
        assert mapper.percent_complete_to_fraction(100) == 1.0

    def test_percent_complete_none_is_zero(self):
        assert mapper.percent_complete_to_fraction(None) == 0.0

    def test_percent_complete_out_of_range_is_clamped(self):
        assert mapper.percent_complete_to_fraction(150) == 1.0
        assert mapper.percent_complete_to_fraction(-10) == 0.0


class TestTaskSelection:
    def test_normal_task_is_mappable(self):
        assert mapper.is_mappable_task(FakeMspTask(UniqueID=1, Name="Design"))

    def test_summary_task_is_skipped(self):
        assert not mapper.is_mappable_task(
            FakeMspTask(UniqueID=1, Name="Design phase", Summary=True)
        )

    def test_inactive_task_is_skipped(self):
        assert not mapper.is_mappable_task(
            FakeMspTask(UniqueID=1, Name="old row", Active=False)
        )

    def test_blank_task_is_skipped(self):
        assert not mapper.is_mappable_task(FakeMspTask(UniqueID=4, Name=""))

    def test_milestone_flag_detection(self):
        task = FakeMspTask(UniqueID=1, Name="Go live", Milestone=True)
        assert mapper.is_milestone_task(task)
        assert not mapper.is_milestone_task(
            FakeMspTask(UniqueID=2, Name="Coding")
        )


class TestTaskMapping:
    def make_task(self, **overrides) -> FakeMspTask:
        defaults = dict(
            UniqueID=1,
            Name="Design",
            Duration=960.0,  # 2 working days of 480 minutes
            PercentComplete=50,
            Start=Y2026,
            Finish=datetime(2026, 6, 3, 17, 0),
        )
        defaults.update(overrides)
        return FakeMspTask(**defaults)

    def test_task_maps_to_domain_object(self):
        task = mapper.map_task(self.make_task(), minutes_per_day=480.0)

        assert task.id == "1"
        assert task.name == "Design"
        assert task.duration_days == 2.0
        assert task.progress == 0.5
        assert task.start == date(2026, 6, 1)
        assert task.finish == date(2026, 6, 3)
        assert task.dependency_ids == []
        assert task.assigned_resource_ids == []

    def test_missing_percent_complete_defaults_to_zero(self):
        task = mapper.map_task(self.make_task(PercentComplete=None))
        assert task.progress == 0.0

    def test_missing_dates_map_to_none(self):
        task = mapper.map_task(self.make_task(Start=None, Finish=None))
        assert task.start is None
        assert task.finish is None

    def test_missing_unique_id_raises(self):
        raw = FakeMspTask(UniqueID=None, Name="orphan")
        with pytest.raises(MspMappingError, match="UniqueID"):
            mapper.map_task(raw)

    def test_missing_name_raises(self):
        raw = FakeMspTask(UniqueID=7, Name="")
        with pytest.raises(MspMappingError, match="name"):
            mapper.map_task(raw)

    def test_finish_before_start_drops_dates_not_crash(self):
        raw = self.make_task(Start=datetime(2026, 6, 10), Finish=datetime(2026, 6, 1))
        task = mapper.map_task(raw)
        assert task.start is None
        assert task.finish is None

    def test_dependencies_read_from_task_dependencies(self):
        raw = self.make_task(
            TaskDependencies=[FakeMspDependency(From=FakeMspTask(UniqueID=99, Name="pred"))]
        )
        task = mapper.map_task(raw)
        assert task.dependency_ids == ["99"]

    def test_assigned_resources_read_from_resources(self):
        raw = self.make_task(
            Resources=[FakeMspResource(UniqueID=5, Name="Alice, max 1")]
        )
        task = mapper.map_task(raw)
        assert task.assigned_resource_ids == ["5"]


class TestMilestoneMapping:
    def test_milestone_maps_to_domain_milestone(self):
        raw = FakeMspTask(
            UniqueID=9,
            Name="Go live",
            Milestone=True,
            Finish=datetime(2026, 7, 15, 17, 0),
        )
        milestone = mapper.map_milestone(raw)

        assert milestone is not None
        assert milestone.id == "9"
        assert milestone.name == "Go live"
        assert milestone.date == date(2026, 7, 15)

    def test_milestone_without_date_is_skipped(self):
        raw = FakeMspTask(UniqueID=9, Name="Go live", Milestone=True, Finish=None)
        assert mapper.map_milestone(raw) is None


class TestResourceMapping:
    def test_resource_maps_with_capacity(self):
        raw = FakeMspResource(UniqueID=3, Name="Bob", MaxUnits=0.5)
        resource = mapper.map_resource(raw)

        assert resource.id == "3"
        assert resource.name == "Bob"
        assert resource.capacity_units == 0.5

    def test_missing_max_units_defaults_to_full_capacity(self):
        raw = FakeMspResource(UniqueID=3, Name="Bob", MaxUnits=None)
        assert mapper.map_resource(raw).capacity_units == 1.0

    def test_missing_name_raises(self):
        with pytest.raises(MspMappingError, match="name"):
            mapper.map_resource(FakeMspResource(UniqueID=3, Name=""))


class TestDeadlineConstraint:
    def test_deadline_maps_to_constraint(self):
        raw = FakeMspTask(
            UniqueID=2, Name="Implementation", Deadline=datetime(2026, 8, 1)
        )
        constraint = mapper.deadline_constraint(raw)

        assert constraint is not None
        assert constraint.kind is ConstraintKind.DEADLINE
        assert constraint.id == "deadline-2"
        assert "2026-08-01" in constraint.description

    def test_no_deadline_maps_to_none(self):
        assert mapper.deadline_constraint(
            FakeMspTask(UniqueID=2, Name="Implementation", Deadline=None)
        ) is None
