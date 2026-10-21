"""Demo Mode tests: the offline fixture must behave like real data.

Everything here runs WITHOUT Microsoft Project, COM, JEV, an API key,
or network access. The demo fixture — like a real read — enters the
product through the one workflow boundary (`load_project(loader)`) and
is analyzed only by the EXISTING pipeline; the demo adds no second
scoring, ranking, or workflow implementation.

Assertions are structural and derived (never hard-coded ranking
results), plus the two properties Demo Mode exists for:

1. determinism — two builds are equal and two full runs are identical;
2. offline completeness — the whole flow works with no external system.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest

from project_decision.application.decision_pipeline import run_decision_pipeline
from project_decision.application.decision_review import MISSING_SCHEDULE_DATA
from project_decision.application.decision_workflow import (
    add_candidate,
    confirm_session,
    load_project,
    remove_candidate,
    run_decision,
    set_planning_period,
)
from project_decision.application.resource_conflicts import (
    ConflictStatus,
    analyze_resource_conflicts,
)
from project_decision.demo import (
    DEMO_CAPTURED_AT,
    DEMO_PROJECT_NAME,
    build_demo_state,
    load_demo_project,
)
from project_decision.domain.planning_period import PlanningPeriod

PERIOD = PlanningPeriod(start_date=date(2026, 6, 1), end_date=date(2026, 6, 12))


def offline_evaluator(session, state):
    return None


def demo_run(state) -> object:
    """load → period → confirm → run — the existing workflow, offline."""
    workflow = confirm_session(set_planning_period(load_project(lambda: state), PERIOD))
    return run_decision(workflow, offline_evaluator)


@pytest.fixture
def demo_state():
    return build_demo_state()


class TestDemoFixture:
    def test_project_facts(self, demo_state):
        assert demo_state.project_id == "DEMO-COMMISSIONING"
        assert demo_state.project_name == DEMO_PROJECT_NAME
        assert demo_state.captured_at == DEMO_CAPTURED_AT
        assert demo_state.project_start == date(2026, 6, 1)
        assert demo_state.project_finish == date(2026, 6, 17)

    def test_two_builds_are_equal_deterministic(self):
        assert build_demo_state() == build_demo_state()

    def test_loader_returns_a_fresh_equal_snapshot(self):
        assert load_demo_project() == load_demo_project()

    def test_scenario_covers_the_demonstration_matrix(self, demo_state):
        by_id = {task.id: task for task in demo_state.tasks}

        # a summary row and a completed task (neither plans itself)
        assert by_id["1"].is_summary is True
        assert by_id["7"].progress == 1.0
        # different slack situations: zero, positive, unknown
        assert by_id["2"].total_slack_days == 0.0
        assert by_id["10"].total_slack_days == 6.0
        assert by_id["9"].total_slack_days is None
        assert by_id["9"].start is None and by_id["9"].finish is None
        # notes present and missing
        assert by_id["4"].notes is None
        assert by_id["3"].notes is not None
        # criticality flags both ways; a dependency chain exists
        assert by_id["2"].is_critical is True
        assert by_id["6"].is_critical is False
        assert by_id["3"].dependency_ids == ["2"]
        assert by_id["9"].dependency_ids == []
        # the demo workforce
        names = {resource.name for resource in demo_state.resources}
        assert {"Alice", "Bob", "Carol"} <= names

    def test_default_candidates_for_the_natural_period(self, demo_state):
        from project_decision.application.candidate_discovery import (
            discover_default_candidates,
        )

        candidates = discover_default_candidates(demo_state, PERIOD)
        # summary "1", completed "7", outside-period "8", undated "9" are
        # honestly excluded; the in-window incomplete work is suggested.
        assert set(candidates) == {"2", "3", "4", "5", "6", "10", "11", "12"}


class TestDemoConflicts:
    def test_shared_resource_overlaps_become_conflicts(self, demo_state):
        analysis = analyze_resource_conflicts(demo_state)
        names = {task.id: task.name for task in demo_state.tasks}
        conflict_pairs = {
            frozenset(pair.task_ids)
            for pair in analysis.conflicts_with_status(ConflictStatus.CONFLICT)
        }
        assert frozenset(("2", "10")) in conflict_pairs  # Alice twice 06-02..03
        assert frozenset(("3", "11")) in conflict_pairs  # Bob twice 06-04..05

    def test_unknown_share_without_dates_is_reported_not_decided(
        self, demo_state
    ):
        analysis = analyze_resource_conflicts(demo_state)
        names = {task.id: task.name for task in demo_state.tasks}
        unknown_ids = {
            task_id
            for pair in analysis.conflicts_with_status(ConflictStatus.UNKNOWN)
            for task_id in pair.task_ids
        }
        assert "9" in unknown_ids  # shared Alice, undated → UNKNOWN


class TestDemoWorkflow:
    def test_full_offline_workflow_runs_through_the_existing_boundary(
        self, demo_state
    ):
        workflow = load_project(lambda: demo_state)
        assert workflow.planning_period is None
        assert workflow.planning_session is None
        assert workflow.confirmed is False

        prepared = set_planning_period(workflow, PERIOD)
        session = prepared.planning_session
        assert session is not None
        confirmed = confirm_session(prepared)
        assert confirmed.confirmed is True

        # The OFFLINE evaluator evaluates nothing (no JEV); the existing
        # pipeline still scores and ranks every candidate provisionally.
        result = run_decision(confirmed, offline_evaluator)
        ranked = result.decision_result.ranked_tasks
        assert [one.rank for one in ranked] == list(
            range(1, len(session.candidate_task_ids) + 1)
        )
        for one in ranked:  # an index in 0..100, never a probability
            assert 0.0 <= one.total_score <= 100.0
        # the provenance of every consequence is honest: provisional
        assert all(
            one.score.consequence_source == "provisional" for one in ranked
        )
        review = result.decision_review
        assert review.candidate_count == len(session.candidate_task_ids)
        assert review.provisional_consequence_count == review.candidate_count

    def test_candidate_shaping_add_and_remove(self, demo_state):
        prepared = set_planning_period(load_project(lambda: demo_state), PERIOD)
        shaped = add_candidate(prepared, "8")  # outside period, still valid
        assert shaped.planning_session.candidate_task_ids[-1] == "8"
        assert shaped.confirmed is False  # editing un-confirms
        shaped = remove_candidate(shaped, "8")
        assert "8" not in shaped.planning_session.candidate_task_ids

    def test_duplicated_and_unknown_candidates_repeat_existing_errors(
        self, demo_state
    ):
        prepared = set_planning_period(load_project(lambda: demo_state), PERIOD)
        with pytest.raises(ValueError, match="already a candidate"):
            add_candidate(prepared, "2")
        with pytest.raises(ValueError, match="unknown task id"):
            add_candidate(prepared, "99")

    def test_undated_addition_stays_unscored_and_requires_review(
        self, demo_state
    ):
        prepared = set_planning_period(load_project(lambda: demo_state), PERIOD)
        shaped = confirm_session(add_candidate(prepared, "9"))
        result = run_decision(shaped, offline_evaluator)
        task_9 = next(
            one for one in result.decision_result.ranked_tasks
            if one.task_id == "9"
        )
        assert task_9.total_score is None  # missing slack is never invented
        review = result.decision_review
        assert "9" in review.tasks_with_missing_schedule
        assert review.review_reason_counts[MISSING_SCHEDULE_DATA] >= 1
        assert review.status is not None

    def test_ranking_is_ordered_by_score_descending(self, demo_state):
        """Order derives from the documented rules (asserted relatively —
        the demo never hard-codes final ranking results)."""
        result = demo_run(demo_state)
        scores = [one.total_score for one in result.decision_result.ranked_tasks]
        assert scores == sorted(scores, reverse=True)


class TestDemoDeterminism:
    def test_two_full_demo_runs_are_identical(self):
        first = demo_run(build_demo_state())
        second = demo_run(build_demo_state())
        assert first == second

    def test_pipeline_called_directly_matches_workflow_result(self, demo_state):
        workflow = confirm_session(
            set_planning_period(load_project(lambda: demo_state), PERIOD)
        )
        direct = run_decision_pipeline(
            demo_state, workflow.planning_session, offline_evaluator
        )
        via_workflow = run_decision(workflow, offline_evaluator)
        # one decision path, two entry points — same result
        assert direct == via_workflow


class TestDemoNeedsNothingExternal:
    def test_demo_module_imports_only_the_domain(self):
        package = (Path(__file__).resolve().parents[1]
                   / "src" / "project_decision" / "demo")
        for path in sorted(package.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module == "__future__" or node.module == "datetime":
                        continue  # stdlib: typing-free date/datetime only
                    assert node.module.startswith("project_decision.domain") or (
                        node.module.startswith("project_decision.demo")
                    ), f"{path.name} imports {node.module}"
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name.startswith("project_decision"), (
                            f"{path.name} imports {alias.name}"
                        )
