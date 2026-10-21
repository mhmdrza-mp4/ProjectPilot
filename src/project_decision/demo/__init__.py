"""Demo Mode: a fully offline, deterministic project fixture.

`load_demo_project()` is a `ProjectLoader` (any callable returning a
``ProjectState``) and plugs into the EXISTING workflow boundary:

    workflow = load_project(load_demo_project)

— exactly the path the live MSP loader takes. No other module changes,
no second workflow, no second pipeline. The fixture lives in
``demo/fixture.py``; see its scenario table for what the demo data
exercises.

Demo mode requires nothing external: no Microsoft Project, no COM, no
JEV, no API key, and no network. The fixture is a plain snapshot of the
domain models, so results are reproducible on every run.
"""

from __future__ import annotations

from project_decision.demo.fixture import (
    DEMO_CAPTURED_AT,
    DEMO_PROJECT_ID,
    DEMO_PROJECT_NAME,
    build_demo_state,
)
from project_decision.domain.project_state import ProjectState

__all__ = [
    "DEMO_CAPTURED_AT",
    "DEMO_PROJECT_ID",
    "DEMO_PROJECT_NAME",
    "build_demo_state",
    "load_demo_project",
]


def load_demo_project() -> ProjectState:
    """Return the demo ``ProjectState`` (a fresh, equal snapshot).

    This is the loader the workflow consumes for Demo Mode; each call
    builds a fresh (and therefore side-effect-free) snapshot whose
    content is fixed — repeated demo runs produce identical results.
    """
    return build_demo_state()
