"""Real-MSP integration test for the TERMINAL UI (``msp`` marked).

Same discipline as the pre-existing live MSP tests:

- **strictly read-only**: Microsoft Project is driven only to open the
  bundled sample (a file shipped with the repo, never a user project).
  Nothing is saved, nothing is written, the application is never quit.
- skips cleanly when Microsoft Project, pywin32, or the sample file is
  missing.
- the dashboard is driven through the REAL application path: state in
  via the existing adapter chain, workflow via the existing decision
  functions, display via the actual Textual widgets (rendered inside
  ``run_test`` — no hand-fed widget data).
- expectations come from ``expected-results.md`` (values the real
  pipeline produced and that were re-verified; never guessed).

Run explicitly:

    pytest -m msp -q tests/test_tui_live.py
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

pytest.importorskip(
    "win32com.client", reason="pywin32 is required for MSP integration tests"
)

pytestmark = pytest.mark.msp

SAMPLE_FILE = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "production-line-commissioning"
    / "Production_Line_Commissioning.mpp"
)

DOC_PERIOD = (datetime.date(2026, 10, 22), datetime.date(2026, 11, 6))
DOC_CANDIDATES = 37
DOC_TOP3 = ["Roofing & cladding", "Structural steel erection", "Facility readiness check"]


@pytest.fixture(scope="module")
def project_state():
    """Read the bundled sample through the REAL adapter chain (read-only)."""
    from project_decision.infrastructure.msp.com_client import MspComClient
    from project_decision.infrastructure.msp.exceptions import (
        MspNoActiveProjectError,
        MspNotAvailableError,
    )
    from project_decision.infrastructure.msp.msp_adapter import MspProjectAdapter

    if not SAMPLE_FILE.is_file():
        pytest.skip("bundled sample file is missing")

    client = MspComClient()
    try:
        client.connect()
    except MspNotAvailableError as exc:
        pytest.skip(f"Microsoft Project is not reachable via COM: {exc}")

    app = client._application
    try:
        app.FileOpenEx(str(SAMPLE_FILE), True)
        adapter = MspProjectAdapter(client)
        return adapter.read_active_project_state(
            captured_at=datetime.datetime(2026, 10, 21, 10, 0)
        )
    except MspNoActiveProjectError as exc:
        pytest.skip(f"could not open the sample in Microsoft Project: {exc}")
    finally:
        # Release our COM references only — never save, never close.
        client.disconnect()


def test_bridge_reads_the_real_sample(project_state):
    from project_decision.tui import data as bridge

    # the bridge's loader is the same public function the app calls on [L]
    assert len(project_state.tasks) == 120
    assert len(project_state.resources) == 10
    low, high = bridge.suggest_period_bounds(project_state)
    assert low is not None and high is not None


async def test_dashboard_shows_the_real_analysis(project_state):
    from textual.widgets import DataTable

    from project_decision.application.decision_review import ReviewStatus
    from project_decision.application.decision_workflow import (
        confirm_session,
        run_decision,
        set_planning_period,
    )
    from project_decision.domain import PlanningPeriod
    from project_decision.tui import data as bridge
    from project_decision.tui.app import DecisionTUI

    def render(widget) -> str:
        return str(widget.render())

    app = DecisionTUI()
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause()
        app._apply_loaded(project_state)
        await pilot.pause()

        # the chip must report a REAL connection (state came from a
        # real COM read moments ago — this claim is earned here)
        assert "LIVE MSP" in render(app.query_one("#connection"))
        assert "CONNECTED" not in render(app.query_one("#connection"))
        assert "Production_Line_Commissioning.mpp" in (
            render(app.query_one("#info_project"))
        )
        project_note = render(app.query_one("#info_project_note"))
        assert "120 activities" in project_note
        assert "10 resources" in project_note

        # [P] → [C] → [A] through the exact workflow paths the keys use
        app._subject = set_planning_period(
            app._subject, PlanningPeriod(start_date=DOC_PERIOD[0], end_date=DOC_PERIOD[1])
        )
        app._subject = confirm_session(app._subject)
        assert len(app._subject.planning_session.candidate_task_ids) == DOC_CANDIDATES

        result = run_decision(app._subject, bridge.offline_evaluator())
        app._apply_result(result)
        await pilot.pause()

        queue = app.query_one("#queue", DataTable)
        assert len(queue.rows) == DOC_CANDIDATES

        ranked = result.decision_result.ranked_tasks
        assert [item.task_name for item in ranked[:3]] == DOC_TOP3
        assert result.decision_review.status is ReviewStatus.REVIEW_REQUIRED

        title = render(app.query_one("#queue_title"))
        assert "REVIEW REQUIRED" in title
        assert "provisional" in title.lower()

        # select the top activity and verify the inspector shows REAL facts
        app._render_inspector(ranked[0].task_id)
        title = render(app.query_one("#insp_title"))
        assert ranked[0].task_name in title
        signals = render(app.query_one("#insp_signals"))
        # offline analysis → honest provisional labeling, never "live"
        assert "PROVISIONAL" in signals
        assert "LIVE JEV" not in signals

        timeline = render(app.query_one("#timeline"))
        assert "TIMELINE — candidates" in timeline
        assert DOC_TOP3[0] in timeline


async def test_dashboard_reports_com_failure_honestly():
    """With no reachable MSP, [L] must surface an error, not fake data."""
    from project_decision.infrastructure.msp.exceptions import MspNoActiveProjectError
    from project_decision.tui.app import DecisionTUI

    app = DecisionTUI()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app._apply_load_error(
            MspNoActiveProjectError("cannot read the active project")
        )
        await pilot.pause()
        connection = str(app.query_one("#connection").render())
        assert "ERROR" in connection
        assert "LIVE MSP" not in connection
        hint = str(app.query_one("#queue_state").render())
        assert hint.startswith("WHAT:")
        assert "open a project in Microsoft Project" in hint
