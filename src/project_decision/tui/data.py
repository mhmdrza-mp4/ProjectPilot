"""Terminal-UI bridge: the ONLY layer between Textual widgets and the
existing application logic.

Deliberate boundaries (mirrors the old thin-UI rule, now for Textual):
- every call here forwards to an EXISTING public function
  (``application.project_loader``, ``application.decision_workflow``,
  ``application.consequence_evaluation``, ``project_decision.config``);
- no scoring, ranking, review, conflict, or JEV rules are re-implemented;
- read-only guarantee: nothing here saves, writes, closes, or mutates a
  Microsoft Project document;
- the evaluator boundary is the existing injected
  ``ConsequenceEvaluator`` shape: OFFLINE mode supplies nothing (the
  scoring layer's own provisional behavior applies), LIVE mode binds the
  existing ``evaluate_session_consequences`` with the configured
  TypeSafe settings or refuses loudly when no key exists;
- import safety: importing this module touches NO COM, network, or
  terminal — everything COM-related happens inside ``load_active_state``.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Callable

from project_decision.application.consequence_evaluation import (
    evaluate_session_consequences,
)
from project_decision.application.decision_pipeline import (
    ConsequenceEvaluator,
)
from project_decision.config import load_typesafe_settings
from project_decision.infrastructure.msp.exceptions import (
    MspError,
    MspNoActiveProjectError,
    MspNotAvailableError,
)

#: Consequence-of-Delay evaluation modes, mirroring the implemented
#: application contract: OFFLINE evaluates nothing (provisional 0.5
#: + review flags), LIVE uses the real configured TypeSafe integration.
OFFLINE_MODE = "OFFLINE (provisional)"
LIVE_MODE = "LIVE JEV"


def sample_project_path() -> str:
    """Absolute path of the bundled sample `.mpp` (no COM, pure path)."""
    return str(
        Path(__file__).resolve().parents[3]
        / "examples"
        / "production-line-commissioning"
        / "Production_Line_Commissioning.mpp"
    )


def sample_project_guide_text() -> str:
    """What the terminal UI tells the user about the bundled sample."""
    return (
        "SAMPLE MSP PROJECT — a real, bundled Microsoft Project file "
        "(~120 activities, 12 phases, 10 resources; industrial "
        "commissioning scenario):\n"
        f"  {sample_project_path()}\n\n"
        "How to analyze it:\n"
        "  1. Open that file in Microsoft Project (e.g. `MSPEDIT "
        "Production_Line_Commissioning.mpp` or File > Open).\n"
        "  2. Keep it the ACTIVE project window there.\n"
        "  3. Here: press [L] to load the active Microsoft Project "
        "plan (read-only), [P] to set the planning period (the sample "
        "guide suggests 2026-10-22 to 2026-11-06), [C] to confirm the "
        "candidate session, then [A] to run the analysis.\n\n"
        "What it is and is not:\n"
        "  - This sample uses the REAL read-only Microsoft Project "
        "integration; it needs Microsoft Project installed and a "
        "project open in it.\n"
        "  - Consequence of Delay stays OFFLINE (provisional 0.5, "
        "flagged for review) unless [J] switches to LIVE JEV with a "
        "configured TYPESAFE_API_KEY.\n"
        "  - The application never opens, saves, or closes any "
        "document.\n\n"
        "Full guide and verified results: "
        "examples/production-line-commissioning/ (README.md and "
        "expected-results.md)."
    )


def load_active_state(
    captured_at: datetime.datetime | None = None,
) -> "object":
    """Read the ACTIVE Microsoft Project through the EXISTING loader.

    Raises ``MspError`` subclasses unchanged: the TUI surfaces them as
    structured states (never fabricated data, never a fake connection).
    """
    from project_decision.application.project_loader import (
        load_active_project,
    )

    if captured_at is None:
        captured_at = datetime.datetime.now()
    return load_active_project(captured_at=captured_at)


def offline_evaluator() -> ConsequenceEvaluator:
    """A consequence evaluator that evaluates NOTHING (JEV off).

    Honest: scoring then applies its own provisional neutral CD and
    review flags — nothing is invented, the analysis stays fully
    usable offline.
    """
    return lambda session, state: None


def live_evaluator(
    env_file_directory: Path | None = None,
) -> tuple[ConsequenceEvaluator, str | None]:
    """The real consequence evaluator, or (None, note) when unconfigured.

    The note explains the offline fallback. The API key itself is
    NEVER returned or displayed.
    """
    settings = load_typesafe_settings(env_file_directory=env_file_directory)
    if settings.api_key is None:
        return None, (
            "LIVE JEV unavailable: no TYPESAFE_API_KEY is configured, "
            "so consequences cannot be evaluated live. Continuing "
            "OFFLINE (provisional 0.5, flagged for review)."
        )
    return (
        lambda session, state: evaluate_session_consequences(
            session, state, settings=settings
        ),
        None,
    )


def evaluator_for_mode(
    mode: str,
    env_file_directory: Path | None = None,
) -> tuple[ConsequenceEvaluator | None, str | None]:
    """Resolve the requested mode to (evaluator, note)."""
    if mode == LIVE_MODE:
        return live_evaluator(env_file_directory=env_file_directory)
    return offline_evaluator(), None


def suggest_period_bounds(state) -> tuple:
    """Sensible default period suggestion from project dates (UI default).

    Same rule the product has always used: the full schedule span
    (earliest task start … latest task finish). Missing dates never
    block: either bound may legitimately be None.
    """
    starts = [task.start for task in state.tasks if task.start is not None]
    finishes = [task.finish for task in state.tasks if task.finish is not None]
    if not starts:
        return None, None
    return min(starts), max(finishes)


def friendly_msp_error(exc: Exception) -> str:
    """A structured WHAT/WHY/NEXT error text (never a stack trace)."""
    if isinstance(exc, MspNoActiveProjectError):
        return (
            "WHAT: Microsoft Project could not be read because no "
            "project is active.\n\n"
            "WHY IT MATTERS: the analysis needs the current schedule "
            "snapshot; nothing is fabricated in its place.\n\n"
            "NEXT: open a project in Microsoft Project and press [L] "
            "again. Nothing was written, saved, or closed."
        )
    if isinstance(exc, MspNotAvailableError):
        return (
            "WHAT: Microsoft Project is not reachable through COM "
            "right now.\n\n"
            "WHY IT MATTERS: without the live snapshot the planner "
            "cannot analyze the current schedule.\n\n"
            "NEXT: make sure Microsoft Project is running and pywin32 "
            "is installed in the SAME environment that runs the app "
            "(`pip install pywin32`), then press [L] again."
        )
    hint = getattr(exc, "args", None)
    if hint:
        return (
            f"WHAT: the active Microsoft Project could not be read "
            f"({exc.__class__.__name__}).\n\n"
            f"WHY IT MATTERS: the analysis requires a complete, "
            f"honest project snapshot.\n\n"
            f"NEXT: {exc}\n\nNothing partial or invented was loaded; "
            f"the previous state (if any) is unchanged."
        )
    return (
        "WHAT: the active Microsoft Project could not be read.\n\n"
        "WHY IT MATTERS: the analysis requires a complete, honest "
        "project snapshot.\n\n"
        "NEXT: reopen or repair the project in Microsoft Project, "
        "then press [L] again. No partial or invented data was loaded."
    )


#: kept for callers that only care whether it is one of ours
MSP_ERRORS = (MspError, MspNoActiveProjectError, MspNotAvailableError)

#: the evaluator adapter type, re-exported for annotations
EvaluatorFactory = Callable[[str], tuple[ConsequenceEvaluator | None, str | None]]
