from datetime import date, datetime

import pytest

from project_decision.domain import Constraint, ProjectState, Task
from tests.conftest import CAPTURED_AT, make_constraint, make_project, make_resource, make_task


class TestProjectCreation:
    def test_valid_project(self):
        project = make_project()

        assert project.id == "P1"
        assert len(project.tasks) == 1
        assert len(project.resources) == 1

    def test_empty_project_allowed(self):
        from project_decision.domain import Project

        project = Project(id="P", name="empty")
        assert project.tasks == []

    def test_empty_id_rejected(self):
        with pytest.raises(ValueError, match="id"):
            make_project(project_id="")

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name"):
            make_project(project_id="P", name="")


class TestProjectRelationships:
    def test_task_may_reference_existing_resource(self):
        project = make_project(task=make_task(assigned_resource_ids=["R1"]), resource=make_resource("R1"))
        assert project.task("T1").assigned_resource_ids == ["R1"]

    def test_task_referencing_unknown_resource_rejected(self):
        with pytest.raises(ValueError, match="unknown resource R9"):
            make_project(task=make_task(assigned_resource_ids=["R9"]))

    def test_task_may_reference_existing_task(self):
        tasks = [make_task("T1"), make_task("T2", dependency_ids=["T1"])]
        project = make_project(tasks=tasks)
        assert project.task("T2").dependency_ids == ["T1"]

    def test_task_referencing_unknown_task_rejected(self):
        with pytest.raises(ValueError, match="unknown task T9"):
            make_project(task=make_task(dependency_ids=["T9"]))

    def test_self_dependency_rejected(self):
        with pytest.raises(ValueError, match="depends on itself"):
            make_project(task=make_task(dependency_ids=["T1"]))

    def test_duplicate_task_ids_rejected(self):
        with pytest.raises(ValueError, match="duplicate task id"):
            make_project(tasks=[make_task("T1"), make_task("T1")])

    def test_duplicate_resource_ids_rejected(self):
        with pytest.raises(ValueError, match="duplicate resource id"):
            make_project(resources=[make_resource("R1"), make_resource("R1")])

    def test_task_lookup_unknown_id_raises_keyerror(self):
        with pytest.raises(KeyError):
            make_project().task("T999")


class TestProjectCaptureState:
    def test_capture_state_snaps_up_all_content(self):
        project = make_project(
            milestones=[],
            constraints=[make_constraint()],
        )
        state = project.capture_state(captured_at=CAPTURED_AT)

        assert isinstance(state, ProjectState)
        assert state.project_id == "P1"
        assert state.captured_at == CAPTURED_AT
        assert len(state.tasks) == len(project.tasks)
        assert len(state.constraints) == 1

    def test_snapshot_is_independent_of_project(self):
        project = make_project()
        state = project.capture_state(captured_at=CAPTURED_AT)

        project.tasks[0].progress = 0.9
        project.tasks.append(Task(id="T2", name="new", duration_days=1))

        assert state.tasks[0].progress == 0.0
        assert len(state.tasks) == 1

    def test_frozen_state_rejects_attribute_change(self):
        from project_decision.domain import ProjectState

        state = ProjectState(project_id="P", captured_at=CAPTURED_AT)
        with pytest.raises(AttributeError):
            state.captured_at = datetime(2027, 1, 1)

    def test_state_requires_capture_time(self):
        from project_decision.domain import ProjectState

        with pytest.raises(ValueError, match="captured_at"):
            ProjectState(project_id="P", captured_at=None)  # type: ignore[arg-type]
