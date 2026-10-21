"""Application-facing project loading: "load this project", no COM knowledge.

Architecture position:

    Microsoft Project (COM)
        ↓  MspComClient (raw COM, infrastructure only)
        ↓  MspProjectAdapter + msp_mapper (infrastructure facade)
        ↓
    load_project / load_active_project        ← THIS BOUNDARY (application)
        ↓
    ProjectState → PlanningSession → run_decision_pipeline

Deliberate boundaries:
- application code never imports ``win32com``/``pythoncom`` and never
  touches a COM object. It EITHER injects an adapter (offline tests,
  fake adapters, DI) via ``load_project``,
  OR calls ``load_active_project`` for the pre-existing real chain;
- strictly READ-ONLY: both functions delegate to the existing
  ``MspProjectAdapter.read_active_project_state`` — that path never
  writes, saves, or closes anything in Microsoft Project;
- the existing adapter/mapper are the SINGLE source of truth: this
  module maps no MSP field itself (no duplicate TotalSlack/Notes/
  Summary/dependency rules) and modifies none of their behavior;
- a FAILED load is never an empty/fake state: the existing
  ``MspError`` hierarchy propagates untouched (the presentational
  translation to user messages already exists in
  ``project_state_service``). A valid EMPTY project therefore stays
  clearly distinguishable from a load failure;
- capture diagnostics are preserved, not invented: the adapter's
  ``capture_warnings`` (omitted references to unmapped rows) are carried
  on the returned ``ProjectState``; an empty tuple means no reference was
  dropped by the mapping, never that the source had none.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from project_decision.domain.project_state import ProjectState

if TYPE_CHECKING:  # declared only for typing; never imported at runtime
    from project_decision.infrastructure.msp import MspProjectAdapter


def load_project(
    adapter: MspProjectAdapter,
    captured_at: datetime | None = None,
) -> ProjectState:
    """Load the project an injected adapter can read as a ProjectState.

    ``adapter`` is duck-typed: any object exposing
    ``read_active_project_state(captured_at=...)`` works (the real
    ``MspProjectAdapter``, or a fake in offline tests). Strictly
    read-only; ``MspError`` failures propagate — no empty/fabricated
    fallback exists anywhere on this path.
    """
    return adapter.read_active_project_state(captured_at=captured_at)


def load_active_project(captured_at: datetime | None = None) -> ProjectState:
    """Load the active project through the real infrastructure chain.

    Convenience for application contexts that want one entry point and
    no adapter knowledge at all: own client, connect, read, disconnect.
    ``connect()`` attaches to (or starts) Microsoft Project's COM
    automation, as ``MspComClient`` documents; expected failures
    (project not installed, nothing open, unreadable data) surface as
    the existing ``MspError`` subclasses — never as an empty state. The
    command never writes, saves, or closes the user's document:
    ``disconnect()`` only releases our COM references.
    """
    # Imported here (not at module top) so importing this module stays
    # entirely free of infrastructure side effects.
    from project_decision.infrastructure.msp import MspComClient, MspProjectAdapter

    client = MspComClient()
    try:
        client.connect()
        return load_project(MspProjectAdapter(client), captured_at=captured_at)
    finally:
        client.disconnect()
