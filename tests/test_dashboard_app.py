"""Streamlit AppTest smoke tests: the dashboard and the decision-problem page.

These run the real Streamlit scripts in-memory (no server): the
application state service is monkeypatched at its source module, so no
test ever touches COM (the script re-imports the patched name when it
executes). They verify the honest-error path, the happy rendering path,
and the core decision-problem flow (author -> review -> save -> listed).

Run from the project root: `python -m pytest tests/test_dashboard_app.py -q`.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from project_decision.application import project_state_service as state_service
from project_decision.application.jev_service import JevCallResult
from project_decision.application.project_state_service import LoadedProjectState
from project_decision.config import TypesafeSettings
from project_decision.domain import ProjectState, Resource, Task
from project_decision.infrastructure.jev import (
    JevChoiceAnswer,
    JevNoulAnswer,
    JevScoreAnswer,
)

DASHBOARD = (
    Path(__file__).resolve().parents[1]
    / "src" / "project_decision" / "presentation" / "dashboard.py"
)


def fake_state() -> ProjectState:
    return ProjectState(
        project_id="Example",
        captured_at=datetime(2026, 10, 20, 9, 0),
        project_name="Example",
        tasks=[Task(id="1", name="Design", duration_days=2.0, progress=0.5)],
        resources=[Resource(id="101", name="Alice")],
        project_start=date(2026, 6, 1),
    )


def patch_load(monkeypatch, result: LoadedProjectState) -> None:
    """Patch the application service: the dashboard script re-imports this name."""
    monkeypatch.setattr(
        state_service, "load_active_project_state", lambda captured_at=None: result
    )


@pytest.fixture(autouse=True)
def no_api_key_in_environment(monkeypatch):
    """Dashboard tests run with no TYPESAFE_API_KEY configured.

    The machine may legitimately have a real key in a local .env (or in
    the environment). Running the dashboard with CWD at the repository
    root loads it into the real process environment, which would leak
    into later tests in the same process (the Jev panel then renders an
    extra "key is configured" message). Each test therefore starts and
    ends without the key; monkeypatch restores the machine's real value
    afterwards.
    """
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)


class TestProjectOverview:
    def test_msp_failure_shows_actionable_error_not_a_crash(self, monkeypatch):
        patch_load(
            monkeypatch,
            LoadedProjectState(state=None, error_message="Microsoft Project is not available."),
        )
        at = AppTest.from_file(DASHBOARD, default_timeout=10).run()

        assert not at.exception
        assert len(at.error) == 1
        assert "Microsoft Project" in at.error[0].value

    def test_loaded_state_renders_summary(self, monkeypatch):
        patch_load(
            monkeypatch,
            LoadedProjectState(state=fake_state(), error_message=None),
        )
        at = AppTest.from_file(DASHBOARD, default_timeout=10).run()

        assert not at.exception
        assert any(header.value == "Example" for header in at.header)
        metrics = {metric.label: metric.value for metric in at.metric}
        assert metrics["Tasks"] == "1"
        assert metrics["In progress"] == "1"
        assert metrics["Missing dates"] == "1"  # the fake task has no start/finish


class TestDecisionProblemsPage:
    def _app(self, monkeypatch, tmp_path) -> AppTest:
        monkeypatch.chdir(tmp_path)  # keep saved files out of the repository
        patch_load(
            monkeypatch,
            LoadedProjectState(state=None, error_message="patched: no project loaded"),
        )
        at = AppTest.from_file(DASHBOARD, default_timeout=15)
        return at

    def test_page_renders_with_empty_draft(self, monkeypatch, tmp_path):
        at = self._app(monkeypatch, tmp_path).run()

        assert not at.exception
        assert any(sub.value == "Saved decision problems" for sub in at.subheader)
        assert any(
            selectbox.label == "Load an existing problem or start a new draft"
            for selectbox in at.selectbox
        )

    def test_required_field_errors_are_listed(self, monkeypatch, tmp_path):
        at = self._app(monkeypatch, tmp_path).run()

        assert not at.exception
        bullets = [markdown.value for markdown in at.markdown]
        assert any("needs a title" in value for value in bullets)
        assert any("needs a description" in value for value in bullets)
        assert any("needs an objective" in value for value in bullets)

    def test_add_alternative_button_adds_a_name_input(self, monkeypatch, tmp_path):
        at = self._app(monkeypatch, tmp_path).run()
        at.button(key="dp:add_alternative").click().run()

        assert not at.exception
        name_inputs = [
            widget.key
            for widget in at.text_input
            if widget.key.startswith("dp:alt:") and widget.key.endswith(":name")
        ]
        assert len(name_inputs) == 1
        at.button(key="dp:add_alternative").click().run()
        name_inputs = [
            widget.key
            for widget in at.text_input
            if widget.key.startswith("dp:alt:") and widget.key.endswith(":name")
        ]
        assert len(name_inputs) == 2

    def _seeded_draft(self) -> dict:
        return {
            "id": "dp-testid",
            "title": "Schedule recovery",
            "description": "A potential schedule issue.",
            "objective": "reduce schedule delay",
            "attach_project": False,
            "alternatives": [
                {
                    "uid": "u1", "id": "u1", "name": "Add resources",
                    "kind": "add_resource_capacity",
                    "description": "add a second engineer to Design",
                },
                {
                    "uid": "u2", "id": "u2", "name": "Resequence tasks",
                    "kind": "resequence_tasks",
                    "description": "run Design before Implementation",
                },
            ],
            "criteria": [
                {
                    "uid": "c1", "id": "c1", "name": "Expected schedule impact",
                    "description": "expected days gained or lost",
                }
            ],
            "constraints": [
                {
                    "uid": "k1", "id": "k1", "kind": "other",
                    "description": "no new hires allowed",
                }
            ],
            "assumptions": [
                {"uid": "a1", "id": "a1", "description": "overtime is possible"}
            ],
        }

    def test_review_shows_ready_and_jev_preview(self, monkeypatch, tmp_path):
        at = self._app(monkeypatch, tmp_path)
        at.session_state["dp_draft"] = self._seeded_draft()
        at.run()

        assert not at.exception
        successes = [s.value for s in at.success]
        assert any("ready for evaluation" in value for value in successes)
        # The structured preview reflects the actual domain data.
        import json

        parsed = json.loads(at.json[0].value)
        assert parsed["title"] == "Schedule recovery"
        assert [a["name"] for a in parsed["alternatives"]] == [
            "Add resources", "Resequence tasks"
        ]
        assert parsed["ready_for_evaluation"] is True
        assert parsed["assumptions"][0]["description"] == "overtime is possible"

    def test_save_writes_local_json_file(self, monkeypatch, tmp_path):
        at = self._app(monkeypatch, tmp_path)
        at.session_state["dp_draft"] = self._seeded_draft()
        at.run()

        assert at.button(key="dp:save").disabled is False
        at.button(key="dp:save").click().run()

        assert not at.exception
        assert len(at.success) == 1
        saved_files = list((tmp_path / "data" / "decision_problems").glob("*.json"))
        assert len(saved_files) == 1
        # The selector now points at the saved problem.
        assert at.selectbox(key="dp_selected").value == "dp-testid"


class TestJevTestPanel:
    """The dev panel: key status, one official question of each type.

    Config and network are patched at their source modules; the panel
    never touches the real API and never displays the key value.
    """

    SECRET_KEY = "sk-panel-secret-123"

    @staticmethod
    def _patch_config(monkeypatch, key: str | None) -> None:
        import project_decision.config as config

        monkeypatch.setattr(
            config, "load_typesafe_settings", lambda *a, **kw: TypesafeSettings(api_key=key)
        )

    @staticmethod
    def _patch_service(monkeypatch, result: JevCallResult) -> None:
        import project_decision.application.jev_service as service

        def fake_send(state, questions, **kwargs):
            return result

        monkeypatch.setattr(service, "send_jev_questions", fake_send)

    def _app(self, monkeypatch, tmp_path) -> AppTest:
        monkeypatch.chdir(tmp_path)
        patch_load(
            monkeypatch,
            LoadedProjectState(state=None, error_message="patched: no project loaded"),
        )
        self._patch_config(monkeypatch, key=None)
        return AppTest.from_file(DASHBOARD, default_timeout=15)

    def test_key_not_configured_shows_guidance_and_disables_send(
        self, monkeypatch, tmp_path
    ):
        at = self._app(monkeypatch, tmp_path).run()

        assert not at.exception
        warnings = [w.value for w in at.warning]
        assert any("TYPESAFE_API_KEY" in value for value in warnings)
        assert at.button(key="jev:send").disabled is True

    def test_successful_call_renders_choice_score_and_noul(
        self, monkeypatch, tmp_path
    ):
        at = self._app(monkeypatch, tmp_path)
        self._patch_config(monkeypatch, key=self.SECRET_KEY)
        self._patch_service(
            monkeypatch,
            JevCallResult(
                ok=True,
                model="jev-latest",
                request_id="req-1",
                input_tokens=42,
                output_tokens=7,
                answers={
                    "project_status": JevChoiceAnswer(
                        choice="at_risk",
                        probabilities={"healthy": 0.1, "at_risk": 0.8, "critical": 0.1},
                        confidence=0.72,
                    ),
                    "schedule_concern": JevScoreAnswer(
                        score=1.7,
                        legend={0: "not concerning", 1: "somewhat concerning",
                                2: "concerning", 3: "very concerning"},
                        probabilities={0: 0.1, 1: 0.5, 2: 0.3, 3: 0.1},
                        confidence=0.44,
                    ),
                    "schedule_risk": JevNoulAnswer(noul=0.87),
                },
            ),
        )
        at.run()
        at.button(key="jev:send").click().run()

        assert not at.exception
        texts = " ".join(
            element.value
            for elements in (at.caption, at.markdown, at.metric, at.success)
            for element in elements
        )
        assert self.SECRET_KEY not in texts  # the key value is never displayed
        assert any(m.label == "Selected option" and m.value == "at_risk" for m in at.metric)
        assert any("Score (expected)" in m.label for m in at.metric)
        assert any("req-1" in c.value for c in at.caption)

    def test_configured_but_failed_call_shows_safe_error(
        self, monkeypatch, tmp_path
    ):
        at = self._app(monkeypatch, tmp_path)
        self._patch_config(monkeypatch, key=self.SECRET_KEY)
        self._patch_service(
            monkeypatch,
            JevCallResult(
                ok=False,
                error_message="Jev authentication failed (HTTP 401); "
                "the configured TYPESAFE_API_KEY was not accepted.",
                error_kind="authentication",
            ),
        )
        at.run()
        at.button(key="jev:send").click().run()

        assert not at.exception
        errors = [e.value for e in at.error]
        assert any("HTTP 401" in value for value in errors)
        assert not any(self.SECRET_KEY in value for value in errors)
