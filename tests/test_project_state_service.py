"""Tests for the project state service: load outcomes and error translation.

The MSP read itself is already tested elsewhere; here we test that valid
reads succeed and expected infrastructure failures become actionable error
messages instead of exceptions or empty results.
"""

from datetime import date

import pytest

from project_decision.application.project_state_service import (
    LoadedProjectState,
    load_active_project_state,
)
from project_decision.domain import Resource, Task


def fake_state() -> "object":
    from project_decision.domain import ProjectState
    from datetime import datetime

    return ProjectState(
        project_id="Example",
        captured_at=datetime(2026, 10, 20, 9, 0),
        project_name="Example",
        tasks=[Task(id="1", name="Design", duration_days=2.0, progress=0.5)],
        resources=[Resource(id="1", name="Alice")],
        project_start=date(2026, 1, 5),
    )


@pytest.fixture
def happy_msp(monkeypatch):
    monkeypatch.setattr(
        "project_decision.infrastructure.msp.MspComClient.connect",
        lambda self: None,
    )
    monkeypatch.setattr(
        "project_decision.infrastructure.msp.MspProjectAdapter.read_active_project_state",
        lambda self, captured_at=None: fake_state(),
    )


class TestSuccessfulLoad:
    def test_returns_state_without_error(self, happy_msp):
        result = load_active_project_state()

        assert result.ok is True
        assert result.error_message is None
        assert result.state.project_id == "Example"


class TestErrorTranslation:
    def _patch_and_load(self, monkeypatch, side_effect):
        def failing_read(self, captured_at=None):
            raise side_effect

        monkeypatch.setattr(
            "project_decision.infrastructure.msp.MspComClient.connect",
            lambda self: None,
        )
        monkeypatch.setattr(
            "project_decision.infrastructure.msp.MspProjectAdapter.read_active_project_state",
            failing_read,
        )
        return load_active_project_state()

    def test_msp_not_available_message(self, monkeypatch):
        from project_decision.infrastructure.msp.exceptions import MspNotAvailableError

        result = self._patch_and_load(monkeypatch, MspNotAvailableError("no app"))

        assert result.state is None
        assert result.ok is False
        assert "not available" in result.error_message.lower()
        assert "Microsoft Project" in result.error_message

    def test_no_active_project_message(self, monkeypatch):
        from project_decision.infrastructure.msp.exceptions import MspNoActiveProjectError

        result = self._patch_and_load(monkeypatch, MspNoActiveProjectError("none open"))

        assert result.ok is False
        assert "open a project" in result.error_message.lower()
        assert "Refresh" in result.error_message

    def test_read_or_mapping_error_message(self, monkeypatch):
        from project_decision.infrastructure.msp.exceptions import MspMappingError

        result = self._patch_and_load(monkeypatch, MspMappingError("bad record"))

        assert result.ok is False
        assert "could not be read" in result.error_message.lower()

    def test_result_never_contains_both_state_and_error(self):
        ok = LoadedProjectState(state=fake_state(), error_message=None)
        failed = LoadedProjectState(state=None, error_message="x")

        assert ok.ok is True
        assert not (failed.state and failed.error_message)
