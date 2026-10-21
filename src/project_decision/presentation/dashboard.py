"""Initial Streamlit dashboard for the Project Decision System (read-only).

Run from the project root:

    streamlit run src/project_decision/presentation/dashboard.py

The dashboard is a thin UI with three tabs:
- "Project overview": never touches COM or Microsoft Project details;
  state loading goes through the application service
  (`application.project_state_service`), metrics through
  `application.project_analysis`; strictly read-only.
- "Decision problems": authoring UI for decision problems (alternatives,
  criteria, constraints, assumptions), backed by the domain models and
  the application JSON store; see presentation/decision_problem_page.py.
- "Jev test (dev)": development panel that verifies the official
  TypeSafe/Jev API integration; see presentation/jev_panel.py. Not
  connected to the decision workflow.
- Unavailable information is shown as "not available", never fabricated.
"""

from __future__ import annotations

import pathlib
import sys

# Allow running via `streamlit run src/...` from a source checkout without
# installing the package into the environment's site-packages.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import streamlit as st

from project_decision.application.project_analysis import (
    ScheduleDelay,
    analyze_project_state,
)
from project_decision.application.project_state_service import LoadedProjectState
from project_decision.application.project_state_service import (
    load_active_project_state,
)
from project_decision.presentation.decision_problem_page import (
    render_decision_problems_page,
)
from project_decision.presentation.jev_panel import render_jev_test_page

_NOT_AVAILABLE = "not available"


def main() -> None:
    st.set_page_config(page_title="Project Decision System", page_icon="📊", layout="wide")
    st.title("Project Decision System")
    st.caption(
        "Decision-support layer around Microsoft Project — read-only view of the "
        "active project. Decisions are made by you, the planner."
    )

    tab_project, tab_problems, tab_jev = st.tabs(
        ["Project overview", "Decision problems", "Jev test (dev)"]
    )
    with tab_project:
        _render_project_overview()
    with tab_problems:
        render_decision_problems_page()
    with tab_jev:
        render_jev_test_page()


def _render_project_overview() -> None:
    result = _load_state()

    if not result.ok:
        st.error(result.error_message)
        _render_sidebar()
        return
    state = result.state
    analysis = analyze_project_state(state)

    st.header(state.project_id)
    _render_dates(state)
    _render_metrics(analysis)
    _render_delay(analysis.schedule_delay)
    _render_task_table(state)
    _render_sidebar()


def _load_state() -> LoadedProjectState:
    """Load on first run; reload only on the Refresh button."""
    refresh = st.button("↻ Refresh from Microsoft Project", type="primary")
    if refresh or "loaded_project_state" not in st.session_state:
        st.session_state["loaded_project_state"] = load_active_project_state()
    return st.session_state["loaded_project_state"]


def _render_sidebar() -> None:
    with st.sidebar:
        st.subheader("Scope")
        st.write(
            "Current stage: read the active Microsoft Project, show basic "
            "project indicators, and author decision problems. JEV "
            "evaluation follows in a later stage."
        )
        st.info("Read-only: the dashboard never modifies or saves the project file.")


def _render_dates(state) -> None:
    """Planned project dates; missing values are shown honestly."""
    st.subheader("Project dates")
    start = state.project_start.isoformat() if state.project_start else _NOT_AVAILABLE
    finish = state.project_finish.isoformat() if state.project_finish else _NOT_AVAILABLE
    status = state.status_date.isoformat() if state.status_date else _NOT_AVAILABLE
    col1, col2, col3 = st.columns(3)
    col1.metric("Planned start", start)
    col2.metric("Planned finish", finish)
    col3.metric("Status date", status)

    if state.baseline_start or state.baseline_finish:
        baseline = " → ".join(
            (state.baseline_start.isoformat() if state.baseline_start else "?",
             state.baseline_finish.isoformat() if state.baseline_finish else "?")
        )
        st.caption(f"Baseline: {baseline}")
    else:
        st.caption("Baseline: not available (no baseline has been saved for this project)")


def _render_metrics(analysis) -> None:
    st.subheader("Task summary")
    completion = (
        f"{analysis.overall_completion * 100:.0f}%"
        if analysis.overall_completion is not None
        else _NOT_AVAILABLE
    )
    cols = st.columns(6)
    cols[0].metric("Tasks", analysis.total_tasks)
    cols[1].metric("Completed", analysis.completed_tasks)
    cols[2].metric("In progress", analysis.in_progress_tasks)
    cols[3].metric("Not started", analysis.not_started_tasks)
    cols[4].metric("Missing dates", analysis.tasks_with_missing_dates)
    cols[5].metric("Completion", completion)
    st.caption(
        "Completion (duration-weighted mean progress) needs tasks with nonzero "
        "durations; task classification is by progress only, not by dates."
    )


def _render_delay(delay: ScheduleDelay) -> None:
    st.subheader("Schedule delay vs. baseline")
    if delay.available:
        st.metric("Delay", f"{delay.delay_days} day(s)")
    else:
        st.metric("Delay", _NOT_AVAILABLE)
    st.caption(delay.explanation)


def _render_task_table(state) -> None:
    st.subheader("Tasks")
    rows = []
    for task in state.tasks:
        rows.append(
            {
                "id": task.id,
                "name": task.name,
                "start": task.start.isoformat() if task.start else _NOT_AVAILABLE,
                "finish": task.finish.isoformat() if task.finish else _NOT_AVAILABLE,
                "duration (days)": round(task.duration_days, 2),
                "progress": f"{task.progress * 100:.0f}%",
                "predecessors": ", ".join(task.dependency_ids) if task.dependency_ids else _NOT_AVAILABLE,
            }
        )
    if not rows:
        st.warning("No mappable tasks were found in this project.")
        return
    st.dataframe(rows, hide_index=True, width="stretch")


if __name__ == "__main__":
    main()
