"""Project Decision Assistant — Streamlit entry point.

Run from the project root:

    streamlit run app/streamlit_app.py

Layering (strictly consumed, never reimplemented):

    Streamlit UI (this package)
        ↓
    DecisionWorkflow  (application.decision_workflow)
        ↓
    DecisionPipeline  (application.decision_pipeline)
        ↓
    Domain / MSP (read-only) / JEV infrastructure

This file contains DEFINITIONS and orchestration only: all rendering
lives in ``app/ui/views.py``, all formatting in ``app/ui/components.py``,
all figures in ``app/ui/charts.py``. Importing this module performs no
MSP connection and no API call; the Streamlit widgets are executed only
when ``main()`` is invoked (i.e. by ``streamlit run``), which also keeps
the module import-safe in tests.
"""

from __future__ import annotations

import pathlib
import sys

# Allow `streamlit run app/streamlit_app.py` from a source checkout:
# streamlit puts the script's directory (app/) on sys.path, not the
# repository root, so add src/ and root explicitly.
_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _path in (str(_ROOT / "src"), str(_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.ui import views  # noqa: E402
from app.ui.styles import APPLICATION_CSS  # noqa: E402


def main() -> None:
    """The coherent product flow with state-aware emphasis.

    Sidebar first (decision mode + scoring parameters feed the run), the
    staleness guard right after it, then one section per workflow step —
    later sections only appear once their prerequisites exist, and the
    dashboard only renders for a CURRENT session/parameter set.
    """
    views.ensure_session_state()
    context = views.render_sidebar()
    views.render_header()
    views.stale_result_check(
        context.scoring_parameters if context.parameters_valid else None
    )
    views.render_project_section(context)
    views.render_period_section()
    views.render_candidates_section()
    views.render_setup_section(context)
    views.render_dashboard(context)


if __name__ == "__main__":
    import streamlit as st

    st.set_page_config(
        page_title="Project Decision Assistant",
        page_icon="🎯",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(APPLICATION_CSS, unsafe_allow_html=True)
    main()
