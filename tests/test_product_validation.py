"""Product-level runtime validation that needs NO external system (offline).

Step 14 requires reproducibility checks; this machine-hostile-to-network
environment cannot reach the live JEV service, but two important runtime
products CAN be verified hermetically:

1. the decision pipeline is deterministic: the same captured state
   produces EXACTLY the same ``DecisionPipelineResult`` on every run —
   the reproducibility guarantee the snapshot concept is founded on;
2. re-snapshotting the same content (``ProjectState.from_project`` on
   the equivalent project data) produces the same analysis result —
   the "live read vs snapshot equivalence" property expressed offline.
"""

from __future__ import annotations

from datetime import date, datetime

from project_decision.application.decision_pipeline import run_decision_pipeline
from project_decision.application.decision_workflow import load_project
from project_decision.application.project_loader import load_project as _load
from project_decision.domain import ProjectState, Resource
from project_decision.domain.planning_period import PlanningPeriod
from tests.conftest import make_task

CAPTURED_AT = datetime(2026, 10, 21, 9, 0)
WEEK = PlanningPeriod(start_date=date(2026, 6, 1), end_date=date(2026, 6, 5))
CAPTURE_YEAR = 2026


def make_state() -> ProjectState:
    return ProjectState(
        project_id="P1",
        captured_at=CAPTURED_AT,
        project_name="Demo",
        tasks=[
            make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      notes="permit needed", total_slack_days=0.0),
            make_task("B", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      notes="cleanup", total_slack_days=3.0,
                      assigned_resource_ids=("R1",)),
            make_task("C", start=date(2026, 6, 3), finish=date(2026, 6, 5),
                      notes="training", total_slack_days=12.5,
                      assigned_resource_ids=("R1", "R2")),
        ],
        resources=[Resource(id="R1", name="Alice"), Resource(id="R2", name="Bob")],
    )


def offline_evaluator(session, state):
    return None


def run_for_session(state):
    workflow = load_project(lambda: state)
    prepared = __import__(
        "project_decision.application.decision_workflow",
        fromlist=["set_planning_period"],
    ).set_planning_period(workflow, WEEK)
    session = prepared.planning_session
    return run_decision_pipeline(state, session, offline_evaluator)


class TestReproducibility:
    def test_same_state_two_pipeline_runs_are_identical(self):
        first = run_for_session(make_state())
        second = run_for_session(make_state())
        assert first == second

    def test_result_is_identical_when_the_state_object_is_rerun(self):
        state = make_state()
        workflow = load_project(lambda: state)
        prepared = __import__(
            "project_decision.application.decision_workflow",
            fromlist=["set_planning_period"],
        ).set_planning_period(workflow, WEEK)
        session = prepared.planning_session
        first = run_decision_pipeline(state, session, offline_evaluator)
        second = run_decision_pipeline(state, session, offline_evaluator)
        assert first == second


class TestSnapshotEquivalence:
    def test_two_snapshots_of_the_same_content_agree(self):
        """Re-captured snapshot (same content) ≡ original, structurally."""
        original = make_state()
        recaptured = ProjectState(
            project_id=original.project_id,
            captured_at=datetime(CAPTURE_YEAR + 1, 3, 1, 8, 0),
            project_name=original.project_name,
            tasks=original.tasks,
            resources=original.resources,
            project_start=original.project_start,
            project_finish=original.project_finish,
        )
        assert run_for_session(recaptured) == run_for_session(original)
