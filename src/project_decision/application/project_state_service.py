"""Project state service: loads a ProjectState for the presentation layer.

Flow position (per docs/02 - Architecture.md):

    Microsoft Project -> MSP COM client -> MSP adapter -> ProjectState
    -> this service   -> Streamlit dashboard

The service delegates the MSP-specific work to the existing
``infrastructure.msp`` facade and translates infrastructure exceptions
into presentation-friendly results, so the Streamlit layer never imports
COM, pywin32, or MSP exception types. It performs no business analysis
(see project_analysis.py) and never modifies Microsoft Project state
(the whole read path is read-only).

Returning a result object instead of raising keeps the dashboard simple:
expected infrastructure problems are data for the UI, not crashes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from project_decision.domain.project_state import ProjectState
from project_decision.infrastructure.msp import MspComClient, MspProjectAdapter
from project_decision.infrastructure.msp.exceptions import (
    MspError,
    MspNoActiveProjectError,
    MspNotAvailableError,
)


@dataclass(frozen=True)
class LoadedProjectState:
    """Outcome of one load attempt.

    Exactly one of ``state`` / ``error_message`` is set: an error never
    degrades into an empty project.
    """

    state: ProjectState | None
    error_message: str | None = None

    @property
    def ok(self) -> bool:
        return self.state is not None and self.error_message is None


def load_active_project_state(
    captured_at: datetime | None = None,
) -> LoadedProjectState:
    """Connect to Microsoft Project, read the active project, disconnect.

    Expected failures produce an actionable message in ``error_message``
    (Microsoft Project missing, no project open, unreadable/mappable data
    problems). Unexpected exceptions propagate — they are not silently
    turned into empty results.
    """
    client = MspComClient()
    try:
        client.connect()
        adapter = MspProjectAdapter(client)
        state = adapter.read_active_project_state(captured_at=captured_at)
    except MspNotAvailableError as exc:
        return LoadedProjectState(
            state=None,
            error_message=(
                "Microsoft Project is not available. Please start Microsoft "
                f"Project and try again. (Details: {exc})"
            ),
        )
    except MspNoActiveProjectError as exc:
        return LoadedProjectState(
            state=None,
            error_message=(
                "No active project was found. Please open a project in "
                f"Microsoft Project and press Refresh. (Details: {exc})"
            ),
        )
    except MspError as exc:
        return LoadedProjectState(
            state=None,
            error_message=(
                "Project data could not be read. This may be a data or "
                f"compatibility problem; details: {exc}"
            ),
        )
    finally:
        client.disconnect()

    return LoadedProjectState(state=state, error_message=None)
