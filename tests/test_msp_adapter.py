"""Unit tests for the MSP adapter, using a fake COM client.

No Microsoft Project, no pywin32, no Windows requirement: the fake client
returns the same COM-like objects the real client returns.
"""

from datetime import datetime, date

import pytest

from project_decision.domain.constraint import ConstraintKind
from project_decision.infrastructure.msp.exceptions import (
    MspNoActiveProjectError,
    MspReadError,
)
from project_decision.infrastructure.msp.msp_adapter import MspProjectAdapter
from tests.fakes.fake_msp import (
    FakeMspDependency,
    FakeMspProject,
    FakeMspResource,
    FakeMspTask,
)


class FakeComClient:
    """Duck-typed stand-in for MspComClient (the adapter's full surface)."""

    def __init__(self, project: FakeMspProject) -> None:
        self._project = project

    def get_active_project(self) -> FakeMspProject:
        return self._project

    def get_minutes_per_day(self) -> float:
        return getattr(self._project, "MinutesPerDay", 480.0)


def demo_project() -> FakeMspProject:
    """Mirrors a small realistic MSP schedule:

        1  Planning          summary     -> skipped
        2  Project Planning  milestone
        3  Design            2 days, 40 % done, assigned to Alice
        4  Implementation    depends on Design, deadline 2026-07-01
        5  archived row      inactive    -> skipped
    """
    alice = FakeMspResource(UniqueID=101, Name="Alice", MaxUnits=1.0)
    bob = FakeMspResource(UniqueID=102, Name="Bob", MaxUnits=0.5)

    tasks = [
        FakeMspTask(UniqueID=1, Name="Planning", Summary=True),
        FakeMspTask(
            UniqueID=2,
            Name="Project Planning",
            Milestone=True,
            Duration=0.0,
            Finish=datetime(2026, 6, 2, 17, 0),
        ),
        FakeMspTask(
            UniqueID=3,
            Name="Design",
            Duration=960.0,
            PercentComplete=40,
            Start=datetime(2026, 6, 3, 8, 0),
            Finish=datetime(2026, 6, 4, 17, 0),
            Resources=[alice],
        ),
        FakeMspTask(
            UniqueID=4,
            Name="Implementation",
            Duration=1440.0,
            Start=datetime(2026, 6, 5, 8, 0),
            Finish=datetime(2026, 6, 9, 17, 0),
            TaskDependencies=[
                FakeMspDependency(
                    From=FakeMspTask(UniqueID=3, Name="Design"),
                    To=FakeMspTask(UniqueID=4, Name="Implementation"),
                )
            ],
            Deadline=datetime(2026, 7, 1, 17, 0),
        ),
        FakeMspTask(UniqueID=5, Name="archived row", Active=False),
    ]
    return FakeMspProject(
        Name="Example Project",
        MinutesPerDay=480.0,
        Tasks=tasks,
        Resources=[alice, bob],
        Start=datetime(2026, 6, 1, 8, 0),
        Finish=datetime(2026, 6, 10, 17, 0),
        StatusDate=datetime(2026, 6, 5, 9, 0),
        BaselineStart=datetime(2026, 6, 1, 8, 0),
        # No baseline finish has been saved in this demo project:
        BaselineFinish=None,
    )


@pytest.fixture
def adapter() -> MspProjectAdapter:
    return MspProjectAdapter(FakeComClient(demo_project()))


class TestProjectDates:
    def test_project_dates_are_mapped(self, adapter: MspProjectAdapter):
        from datetime import date as date_type

        project = adapter.read_active_project()

        assert project.project_start == date_type(2026, 6, 1)
        assert project.project_finish == date_type(2026, 6, 10)
        assert project.status_date == date_type(2026, 6, 5)
        assert project.baseline_start == date_type(2026, 6, 1)

    def test_missing_baseline_is_represented_as_none(self, adapter: MspProjectAdapter):
        project = adapter.read_active_project()

        # The demo project has no baseline finish; it must stay None
        # (not invented, not defaulted), so delay metrics report honestly.
        assert project.baseline_finish is None

    def test_state_carries_project_dates(self, adapter: MspProjectAdapter):
        state = adapter.read_active_project_state()

        assert state.project_start is not None
        assert state.baseline_finish is None
        assert state.project_name == "Example Project"


class TestReadActiveProject:
    def test_returns_domain_project(self, adapter: MspProjectAdapter):
        project = adapter.read_active_project()

        assert project.id == "Example Project"
        assert project.name == "Example Project"

    def test_maps_task_fields(self, adapter: MspProjectAdapter):
        project = adapter.read_active_project()
        design = project.task("3")

        assert design.name == "Design"
        assert design.duration_days == 2.0
        assert design.progress == pytest.approx(0.4)
        assert design.start == date(2026, 6, 3)
        assert design.finish == date(2026, 6, 4)

    def test_skips_summary_and_inactive_tasks(self, adapter: MspProjectAdapter):
        project = adapter.read_active_project()

        assert [task.id for task in project.tasks] == ["2", "3", "4"]

    def test_dependency_and_assignment_are_preserved(self, adapter: MspProjectAdapter):
        project = adapter.read_active_project()

        implementation = project.task("4")
        assert implementation.dependency_ids == ["3"]
        design = project.task("3")
        assert design.assigned_resource_ids == ["101"]

    def test_dependency_on_skipped_summary_row_is_dropped(self):
        raw = demo_project()
        raw.Tasks.append(
            FakeMspTask(
                UniqueID=10,
                Name="Depends on summary",
                TaskDependencies=[
                    FakeMspDependency(From=FakeMspTask(1, "Planning"),
                                      To=FakeMspTask(10, "Depends on summary"))
                ],
            )
        )
        adapter = MspProjectAdapter(FakeComClient(raw))
        project = adapter.read_active_project()

        task = next(t for t in project.tasks if t.id == "10")
        assert task.dependency_ids == []
        # The omission is counted and exposed, never silent.
        assert adapter.last_capture_warnings
        assert "predecessor reference" in adapter.last_capture_warnings[0]
        assert "1 " in adapter.last_capture_warnings[0]

    def test_assignment_to_an_unmapped_resource_is_dropped_and_reported(self):
        raw = demo_project()
        raw.Tasks.append(
            FakeMspTask(
                UniqueID=11,
                Name="Ghost resource",
                Duration=480.0,
                Resources=[FakeMspResource(UniqueID=999, Name="Ghost")],
            )
        )
        adapter = MspProjectAdapter(FakeComClient(raw))
        project = adapter.read_active_project()

        task = next(t for t in project.tasks if t.id == "11")
        assert task.assigned_resource_ids == []
        assert any(
            "resource-assignment reference" in warning
            for warning in adapter.last_capture_warnings
        )

    def test_complete_mapping_reports_no_capture_warnings(self, adapter):
        adapter.read_active_project()

        assert adapter.last_capture_warnings == ()
        assert adapter.read_active_project_state().capture_warnings == ()

    def test_state_carries_the_capture_warnings(self):
        raw = demo_project()
        raw.Tasks.append(
            FakeMspTask(
                UniqueID=10,
                Name="Depends on summary",
                TaskDependencies=[
                    FakeMspDependency(From=FakeMspTask(1, "Planning"),
                                      To=FakeMspTask(10, "Depends on summary"))
                ],
            )
        )
        adapter = MspProjectAdapter(FakeComClient(raw))
        state = adapter.read_active_project_state()

        assert state.capture_warnings == adapter.last_capture_warnings
        assert state.capture_warnings  # the diagnostic reaches the snapshot

    def test_milestone_appears_in_tasks_and_milestones(self, adapter: MspProjectAdapter):
        project = adapter.read_active_project()

        assert [m.id for m in project.milestones] == ["2"]
        assert [m.date for m in project.milestones] == [date(2026, 6, 2)]
        milestone_task = project.task("2")
        assert milestone_task.duration_days == 0.0

    def test_resources_are_mapped(self, adapter: MspProjectAdapter):
        project = adapter.read_active_project()

        assert [r.name for r in project.resources] == ["Alice", "Bob"]
        assert project.resources[1].capacity_units == 0.5

    def test_deadline_becomes_constraint(self, adapter: MspProjectAdapter):
        project = adapter.read_active_project()

        assert len(project.constraints) == 1
        constraint = project.constraints[0]
        assert constraint.kind is ConstraintKind.DEADLINE
        assert constraint.id == "deadline-4"
        assert "2026-07-01" in constraint.description


class TestReadActiveProjectState:
    def test_state_snapshot(self, adapter: MspProjectAdapter):
        captured_at = datetime(2026, 10, 20, 9, 0)
        state = adapter.read_active_project_state(captured_at=captured_at)

        assert state.project_id == "Example Project"
        assert state.captured_at == captured_at
        assert len(state.tasks) == 3

    def test_state_defaults_capture_time_to_now(self, adapter: MspProjectAdapter):
        state = adapter.read_active_project_state()

        assert state.captured_at.date() == datetime.now().date()


class TestErrorTranslation:
    def test_no_active_project_raises_domain_error(self):
        class NoProjectClient(FakeComClient):
            def get_active_project(self):
                raise MspNoActiveProjectError("no active project")

        adapter = MspProjectAdapter(NoProjectClient(demo_project()))
        with pytest.raises(MspNoActiveProjectError):
            adapter.read_active_project()

    def test_unreadable_tasks_raise_read_error(self):
        class BrokenTasksProject:
            Name = "Broken"

            @property
            def Tasks(self):  # noqa: N802 - mirrors COM naming
                raise OSError("COM blew up")

            Resources = []

        adapter = MspProjectAdapter(FakeComClient(BrokenTasksProject()))
        with pytest.raises(MspReadError, match="task list"):
            adapter.read_active_project()

    def test_unreadable_resources_raise_read_error(self):
        class BrokenResourcesProject:
            Name = "Broken"

            @property
            def Resources(self):  # noqa: N802 - mirrors COM naming
                raise OSError("COM blew up")

            Tasks = []

        adapter = MspProjectAdapter(FakeComClient(BrokenResourcesProject()))
        with pytest.raises(MspReadError, match="resource list"):
            adapter.read_active_project()

    def test_unreadable_name_raises_read_error(self):
        class BrokenNameProject:
            @property
            def Name(self):  # noqa: N802 - mirrors COM naming
                raise OSError("COM blew up")

        adapter = MspProjectAdapter(FakeComClient(BrokenNameProject()))
        with pytest.raises(MspReadError, match="name"):
            adapter.read_active_project()
