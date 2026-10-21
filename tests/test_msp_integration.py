"""Integration tests against a REAL Microsoft Project via COM.

These tests require:
- Windows
- Microsoft Project installed (any recent version)
- pywin32 installed in the active environment
- a project open in Microsoft Project (the integration tests read the
  ACTIVE project, but do not modify it)

They are excluded from the normal suite and marked with ``msp``:
- ``pytest``            -> skipped
- ``pytest -m msp``     -> run only these

The tests skip themselves automatically when Microsoft Project is not
reachable, so they never fail just because Microsoft Project is missing.
"""

from __future__ import annotations

from datetime import datetime

import pytest

pytest.importorskip(
    "win32com.client", reason="pywin32 is required for MSP integration tests"
)

pytestmark = pytest.mark.msp


@pytest.fixture(scope="module")
def adapter():
    from project_decision.infrastructure.msp import MspComClient, MspProjectAdapter
    from project_decision.infrastructure.msp.exceptions import MspNoActiveProjectError

    client = MspComClient()
    if not client.is_available():
        pytest.skip("Microsoft Project is not reachable via COM")
    try:
        factory_adapter = MspProjectAdapter(client)
        factory_adapter.read_active_project()  # fail fast if no project open
    except MspNoActiveProjectError:
        pytest.skip("no active project open in Microsoft Project")
    except Exception:
        client.disconnect()
        raise
    yield factory_adapter
    client.disconnect()


class TestRealMspRead:
    def test_active_project_has_a_name(self, adapter):
        project = adapter.read_active_project()
        assert project.name
        assert project.id == project.name

    def test_tasks_have_essential_fields(self, adapter):
        project = adapter.read_active_project()

        for task in project.tasks:
            assert task.id
            assert task.name
            assert task.duration_days >= 0.0
            assert 0.0 <= task.progress <= 1.0

    def test_task_dates_are_consistent(self, adapter):
        project = adapter.read_active_project()

        for task in project.tasks:
            if task.start is not None and task.finish is not None:
                assert task.finish >= task.start

    def test_dependencies_and_assignments_resolve(
        self, adapter
    ):
        project = adapter.read_active_project()
        task_ids = {task.id for task in project.tasks}
        resource_ids = {resource.id for resource in project.resources}

        for task in project.tasks:
            assert set(task.dependency_ids) <= task_ids
            assert set(task.assigned_resource_ids) <= resource_ids

    def test_state_snapshot_matches_project(self, adapter):
        captured_at = datetime.now()
        state = adapter.read_active_project_state(captured_at=captured_at)

        project = adapter.read_active_project()
        assert state.project_id == project.name
        # The schedule may change between the two reads in a live session;
        # only structure is asserted here. Both reads must succeed.
        assert captured_at == state.captured_at
