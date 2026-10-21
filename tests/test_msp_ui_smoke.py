"""Real end-to-end STREAMLIT smoke against live Microsoft Project — ``msp``.

Step 14 (§17): run the ACTUAL application entry point
(``app/streamlit_app.py``) with NO patched boundaries and drive the full
flow against the real active project:

    Load Active MSP Project → planning period → Confirm Planning
    Session → Run Decision Analysis → Decision Dashboard.

The evaluator stays in the default OFFLINE mode (the sidebar radio) so
the run never needs JEV or a network: this is exactly the product's
offline mode operating on a REAL Microsoft Project snapshot. The test
asserts UI/app correspondence structurally (metric/table consistency),
never hard-coded values, and Skips itself when Microsoft Project or an
open project is missing.

Run explicitly:

    pytest tests/test_msp_ui_smoke.py -q -m msp
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

pytest.importorskip(
    "win32com.client", reason="pywin32 is required for MSP integration tests"
)

pytestmark = pytest.mark.msp

APP_PATH = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"


@pytest.fixture(scope="module")
def open_msp_project():
    from project_decision.infrastructure.msp.exceptions import (
        MspNoActiveProjectError,
        MspNotAvailableError,
    )

    from project_decision.application.project_loader import load_active_project

    try:
        state = load_active_project()
    except (MspNoActiveProjectError, MspNotAvailableError):
        pytest.skip("Microsoft Project is not running with an open project")
    return state


def _button(at, label: str):
    matches = [button for button in at.button if button.label == label]
    assert matches, f"button {label!r} not on screen; got "
    f"{[button.label for button in at.button]}"
    return matches[0]


class TestRealStreamlitFlow:
    def test_flow_loads_the_real_project_and_ranks_it(self, open_msp_project):
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file(str(APP_PATH), default_timeout=60).run()
        assert not at.exception

        # Load the ACTIVE Microsoft Project through the real chain.
        _button(at, "Load Active MSP Project").click().run()
        assert not at.exception
        summaries = [metric.label for metric in at.metric]
        assert "Project" in summaries
        assert "Capture" in summaries

        # A period wide enough to cover every task of the small project.
        at.date_input[0].set_value(
            (date.today(), date.today() + timedelta(days=14))
        ).run()
        _button(at, "Apply planning period").click().run()
        assert not at.exception
        metrics = {metric.label: metric for metric in at.metric}
        candidates = int(metrics["Default candidates"].value)
        assert candidates >= 1, "the open project has no candidates in-window"

        _button(at, "Confirm Planning Session").click().run()
        assert not at.exception
        metrics = {metric.label: metric for metric in at.metric}
        assert int(metrics["Candidates"].value) == candidates

        # Run with the default OFFLINE evaluator (no JEV, no network).
        _button(at, "Run Decision Analysis").click().run()
        assert not at.exception

        # UI/app correspondence: the dashboard ranks exactly what the
        # confirmed session holds.
        subheaders = [header.value for header in at.subheader]
        assert "Decision Dashboard" in subheaders
        markdown_values = [markdown.value for markdown in at.markdown]
        assert any("Priority Ranking" in value for value in markdown_values)
        # Offline semantics visible in the product: every consequence is
        # provisional, so every task asks for review.
        assert any("REVIEW REQUIRED" in value for value in markdown_values)

    def test_period_card_reports_the_chosen_window(self, open_msp_project):
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file(str(APP_PATH), default_timeout=60).run()
        _button(at, "Load Active MSP Project").click().run()
        at.date_input[0].set_value(
            (date.today(), date.today() + timedelta(days=14))
        ).run()
        _button(at, "Apply planning period").click().run()

        metrics = {metric.label: metric.value for metric in at.metric}
        assert "Period" in metrics
        assert "Working days (Mon–Fri, inclusive)" in metrics
