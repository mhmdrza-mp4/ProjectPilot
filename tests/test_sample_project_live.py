"""Real-MSP integration test for the bundled sample project (``msp``).

Follows the same discipline as the pre-existing live MSP tests:

- **strictly read-only**: it drives Microsoft Project only to open the
  bundled sample (a file shipped with the repo, never a user project),
  read it via the existing adapter chain, and close it WITHOUT saving.
  Never quits the whole application and never touches unrelated
  open projects.
- skips cleanly when Microsoft Project, pywin32, or the sample file is
  missing, so the default hermetic suite is unaffected.
- expectations are derived from the actual read; values documented in
  ``examples/production-line-commissioning/expected-results.md`` are
  regression-tested only for what the real pipeline produced (never
  guessed, never invented).

Run explicitly:

    pytest -m msp -q tests/test_sample_project_live.py
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

# Values verified in expected-results.md (documented, not speculatively
# hard-coded — they come from the ACTUAL pipeline run on this sample).
DOC_PERIOD_START = datetime.date(2026, 10, 22)
DOC_PERIOD_END = datetime.date(2026, 11, 6)
DOC_CANDIDATES = 37


@pytest.fixture(scope="module")
def project_state():
    """Load the bundled sample through the real MSP adapter (read-only).

    The sample stays OPEN in Microsoft Project after the read, matching
    the workflow the application itself documents ("open the sample, make
    it active, then Load Active MSP Project"). We never modify any user
    project, never save, and never close the whole application.
    """
    from project_decision.infrastructure.msp.com_client import MspComClient
    from project_decision.infrastructure.msp.exceptions import (
        MspNoActiveProjectError,
        MspNotAvailableError,
    )
    from project_decision.infrastructure.msp.msp_adapter import MspProjectAdapter

    if not SAMPLE_FILE.is_file():
        pytest.skip(
            "bundled sample file is missing; generate it with "
            "`python scripts/generate_sample_msp.py --i-understand`"
        )

    client = MspComClient()
    try:
        client.connect()
    except MspNotAvailableError as exc:
        pytest.skip(f"Microsoft Project is not reachable via COM: {exc}")

    app = client._application
    try:
        # A dedicated open of OUR OWN sample file: it never overwrites,
        # never saves, and stays as the active document for the rest of
        # this live-MSP test session so downstream test modules that
        # expect an open project (the pre-existing msp tests) can run.
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


def test_sample_opens_with_real_content(project_state):
    assert project_state.project_id == "Production_Line_Commissioning.mpp"
    assert len(project_state.tasks) >= 100
    assert len(project_state.resources) >= 10


def test_capture_is_complete(project_state):
    assert project_state.capture_warnings == ()


def test_dependencies_are_preserved_directed_and_resolve(project_state):
    from project_decision.application.schedule_analysis import analyze_schedule

    id_set = {task.id for task in project_state.tasks}
    for task in project_state.tasks:
        for dep in task.dependency_ids:
            assert dep != task.id, "task must not depend on itself"
            assert dep in id_set, "every predecessor must resolve"

    analysis = analyze_schedule(project_state)
    assert analysis.issues == ()
    assert any(entry.successor_count > 0 for entry in analysis.schedules.values())


def test_planning_window_returns_useful_analysis(project_state):
    from project_decision.application.decision_pipeline import (
        run_decision_pipeline,
    )
    from project_decision.application.planning_session import (
        create_planning_session,
    )
    from project_decision.domain.planning_period import PlanningPeriod

    period = PlanningPeriod(start_date=DOC_PERIOD_START, end_date=DOC_PERIOD_END)
    session = create_planning_session(project_state, period)
    assert len(session.candidate_task_ids) == DOC_CANDIDATES

    result = run_decision_pipeline(project_state, session, lambda s, st: None)
    ranked = result.decision_result.ranked_tasks
    assert len(ranked) == DOC_CANDIDATES
    # Every candidate was scored (nothing silently dropped).
    assert all(item.score is not None for item in ranked)
    # Offline evaluator ⇒ every candidate CD is flagged provisional.
    assert result.decision_review.provisional_consequence_count == DOC_CANDIDATES
    assert result.decision_review.unscored_task_ids == ()


def test_resource_conflict_scenarios_are_detected(project_state):
    from project_decision.application.planning_session import (
        create_planning_session,
    )
    from project_decision.application.resource_conflicts import (
        ConflictStatus,
        analyze_resource_conflicts,
    )
    from project_decision.domain.planning_period import PlanningPeriod

    period = PlanningPeriod(start_date=DOC_PERIOD_START, end_date=DOC_PERIOD_END)
    session = create_planning_session(project_state, period)
    analysis = analyze_resource_conflicts(project_state)

    # Conflicts exist, and they involve shared resources our sample
    # deliberately overbooks to trigger the detector.
    conflicts = [
        conflict
        for conflict in analysis.conflicts
        if conflict.status == ConflictStatus.CONFLICT
    ]
    assert conflicts, "expected real conflict pairs on the sample"
    names = {name for conflict in conflicts for name in conflict.resource_names}
    expected_shared = {
        "Mechanical Fitter",
        "Electrician",
        "Logistics Coordinator",
    }
    assert names & expected_shared, (
        f"expected at least one of {sorted(expected_shared)} among "
        f"conflicting resources, got {sorted(names)}"
    )

    # Candidate-scoped conflicts exist too (the pairs the UI surfaces).
    candidate_ids = set(session.candidate_task_ids)
    in_session = [
        conflict
        for conflict in conflicts
        if set(conflict.task_ids) <= candidate_ids
    ]
    assert in_session, "expected conflicts fully inside the candidate set"


def test_ranking_uses_the_real_scoring_implementation(project_state):
    from project_decision.application.decision_pipeline import (
        run_decision_pipeline,
    )
    from project_decision.application.planning_session import (
        create_planning_session,
    )
    from project_decision.domain.planning_period import PlanningPeriod

    period = PlanningPeriod(start_date=DOC_PERIOD_START, end_date=DOC_PERIOD_END)
    session = create_planning_session(project_state, period)
    result = run_decision_pipeline(project_state, session, lambda s, st: None)
    # Exactly the top 3 verified in expected-results.md, produced by the
    # real scoring implementation against the captured snapshot.
    names = [item.task_name for item in result.decision_result.ranked_tasks[:3]]
    assert names == [
        "Roofing & cladding",
        "Structural steel erection",
        "Facility readiness check",
    ]
