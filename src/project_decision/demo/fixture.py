"""The controlled demo fixture: "Production Line Commissioning".

A small, REALISTIC project snapshot built ENTIRELY from the existing
domain models (``ProjectState``, ``Task``, ``Resource``, ``Milestone``)
— nothing here bypasses the workflow or the pipeline. It exists so the
product can be explored completely offline: no Microsoft Project, no
COM, no JEV, no API key, no network.

Determinism: every value is fixed (dates, progress, slack, resources,
notes) — two calls to ``build_demo_state()`` produce equal states, and
therefore the workflow plus the decision pipeline produces EXACTLY the
same results on every run. The final ranking is never hard-coded
anywhere: it is always computed by the existing pipeline.

The scenario (ids as MSP-like row numbers, June 2026, Mon–Fri):

  1  "Commissioning"                      SUMMARY row (grouping only)
  2    Stress-test robot cell             3d 06-01..03 — Alice — slack 0, critical
  3      Calibrate cell sensors           2d 06-04..05 — Bob   — slack 0, critical — dep 2
  4        Integration QA pass            2d 06-08..09 — Alice — slack 0, critical — dep 3 — NO notes
  5      Resolve punch-list items         2d 06-08..09 — Bob   — slack 3 — dep 3
  6    Prepare shift handover docs        1d 06-04    — Carol — slack 8 — NO notes
  7    Procure spare actuators            3d 06-01..03 — Carol — slack 0 — COMPLETED
  8    Post-go-live hypercare             3d 06-15..17 — Bob   — slack 2 — dep 4 (outside a 06-01..12 period)
  9    Update control-room floor plan     2d UNDATED — Alice — slack unknown (missing schedule data)
 10    File supplier compliance certs     2d 06-02..03 — Alice+Bob — slack 6
 11    Line-speed load test               2d 06-04..05 — Bob   — slack 1 — dep 2
 12    Legacy documentation cleanup       1d 06-10    — Carol — slack 7 — NO notes

What this deliberately exercises (each is a documented product rule,
never a demo-specific behavior):

- a summary row that the workflow never treats as work;
- completed work that needs no planning decision;
- an activity outside the suggested period (manually addable);
- an UNDATED activity with unknown slack (not suggested; if added
  manually it stays honestly unscored and requires review);
- schedule pressure from zero AND positive slack;
- successor impact (tasks 2 and 3 fan out to two successors each);
- manually shared resources CREATE real conflict warnings: Alice
  overlaps herself on 06-02..03 (tasks 2 and 10), Bob on 06-04..05
  (tasks 3 and 11), and undated task 9 shares Alice so the share stays
  UNKNOWN until dates exist;
- notes that would matter to JEV (high operational impact vs. low), and
  three activities with NO notes (provisional consequence — the offline
  evaluator never invents one);
- a milestone (the safety sign-off the notes talk about).

With the natural period 06-01..06-12 the top score is a genuine TIE
between tasks 2 and 3 (equal SP, SI and CD) so the documented ranking
tie-breaks are exercised, not just the sum.
"""

from __future__ import annotations

from datetime import date, datetime

from project_decision.domain.milestone import Milestone
from project_decision.domain.project_state import ProjectState
from project_decision.domain.resource import Resource
from project_decision.domain.task import Task

DEMO_PROJECT_ID = "DEMO-COMMISSIONING"
DEMO_PROJECT_NAME = "Production Line Commissioning (Demo)"
DEMO_CAPTURED_AT = datetime(2026, 6, 1, 8, 0)

_D = date  # readable alias for the fixture rows below


def build_demo_state() -> ProjectState:
    """Build the demo ``ProjectState`` (fixed, deterministic, no I/O)."""
    return ProjectState(
        project_id=DEMO_PROJECT_ID,
        captured_at=DEMO_CAPTURED_AT,
        project_name=DEMO_PROJECT_NAME,
        project_start=_D(2026, 6, 1),
        project_finish=_D(2026, 6, 17),
        tasks=[
            Task(
                id="1", name="Commissioning", duration_days=12.0,
                start=_D(2026, 6, 1), finish=_D(2026, 6, 17),
                is_summary=True,
            ),
            Task(
                id="2", name="Stress-test robot cell", duration_days=3.0,
                start=_D(2026, 6, 1), finish=_D(2026, 6, 3),
                assigned_resource_ids=["1"],
                total_slack_days=0.0, is_critical=True,
                notes=(
                    "Stress test decides whether the robot cell can be "
                    "committed to production; failure stalls all "
                    "downstream commissioning."
                ),
            ),
            Task(
                id="3", name="Calibrate cell sensors", duration_days=2.0,
                start=_D(2026, 6, 4), finish=_D(2026, 6, 5),
                dependency_ids=["2"], assigned_resource_ids=["2"],
                total_slack_days=0.0, is_critical=True,
                notes=(
                    "Calibration drift here invalidates every downstream "
                    "QA run scheduled during commissioning."
                ),
            ),
            Task(
                id="4", name="Integration QA pass", duration_days=2.0,
                start=_D(2026, 6, 8), finish=_D(2026, 6, 9),
                dependency_ids=["3"], assigned_resource_ids=["1"],
                total_slack_days=0.0, is_critical=True,
                # no notes: consequence of delay stays provisional
            ),
            Task(
                id="5", name="Resolve punch-list items", duration_days=2.0,
                start=_D(2026, 6, 8), finish=_D(2026, 6, 9),
                dependency_ids=["3"], assigned_resource_ids=["2"],
                total_slack_days=3.0, is_critical=False,
                notes=(
                    "Electrical punch list; mostly cosmetic, but two "
                    "items block the safety sign-off."
                ),
            ),
            Task(
                id="6", name="Prepare shift handover docs", duration_days=1.0,
                start=_D(2026, 6, 4), finish=_D(2026, 6, 4),
                assigned_resource_ids=["3"],
                total_slack_days=8.0, is_critical=False,
                # no notes: consequence of delay stays provisional
            ),
            Task(
                id="7", name="Procure spare actuators", duration_days=3.0,
                progress=1.0,
                start=_D(2026, 6, 1), finish=_D(2026, 6, 3),
                assigned_resource_ids=["3"],
                total_slack_days=0.0, is_critical=True,
                notes="Completed on time; spares stored in the line cage.",
            ),
            Task(
                id="8", name="Post-go-live hypercare", duration_days=3.0,
                start=_D(2026, 6, 15), finish=_D(2026, 6, 17),
                dependency_ids=["4"], assigned_resource_ids=["2"],
                total_slack_days=2.0, is_critical=False,
                notes="Standing support window after go-live.",
            ),
            Task(
                id="9", name="Update control-room floor plan",
                duration_days=2.0,
                # no dates at all: unknowably schedulable
                assigned_resource_ids=["1"],
                total_slack_days=None, is_critical=None,
                notes="Old floor plan; low operational impact.",
            ),
            Task(
                id="10", name="File supplier compliance certificates",
                duration_days=2.0,
                start=_D(2026, 6, 2), finish=_D(2026, 6, 3),
                assigned_resource_ids=["1", "2"],
                total_slack_days=6.0, is_critical=False,
                notes=(
                    "Vendor certificates must reach the safety office "
                    "before commissioning sign-off; missing papers "
                    "delay the whole line."
                ),
            ),
            Task(
                id="11", name="Line-speed load test", duration_days=2.0,
                start=_D(2026, 6, 4), finish=_D(2026, 6, 5),
                dependency_ids=["2"], assigned_resource_ids=["2"],
                total_slack_days=1.0, is_critical=False,
                notes=(
                    "Proves the conveyor can hold the contracted "
                    "throughput for a full shift."
                ),
            ),
            Task(
                id="12", name="Legacy documentation cleanup",
                duration_days=1.0,
                start=_D(2026, 6, 10), finish=_D(2026, 6, 10),
                assigned_resource_ids=["3"],
                total_slack_days=7.0, is_critical=False,
                # no notes: consequence of delay stays provisional
            ),
        ],
        resources=[
            Resource(id="1", name="Alice", capacity_units=1.0),
            Resource(id="2", name="Bob", capacity_units=1.0),
            Resource(id="3", name="Carol", capacity_units=1.0),
        ],
        milestones=[
            Milestone(id="13", name="Safety sign-off", date=_D(2026, 6, 12)),
        ],
    )
