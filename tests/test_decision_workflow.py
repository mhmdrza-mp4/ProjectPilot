"""Tests for the decision workflow (fully offline)."""

import ast
import inspect
from copy import deepcopy
from datetime import date, datetime

import pytest

from project_decision.application.decision_review import ReviewStatus
from project_decision.application.decision_pipeline import (
    DecisionPipelineResult,
    run_decision_pipeline,
)
import project_decision.application.decision_workflow as workflow_module
from project_decision.application.decision_workflow import (
    DecisionWorkflowState,
    add_candidate,
    confirm_session,
    load_project,
    remove_candidate,
    result_is_stale,
    run_decision,
    set_planning_period,
)
from project_decision.application.candidate_discovery import (
    discover_default_candidates,
)
from project_decision.application.planning_session import create_planning_session
from project_decision.application.priority_scoring import ScoringParameters
from project_decision.domain import ProjectState
from project_decision.domain.planning_period import PlanningPeriod
from tests.conftest import make_task

CAPTURED_AT = datetime(2026, 10, 21, 9, 0)
WEEK = PlanningPeriod(start_date=date(2026, 6, 1), end_date=date(2026, 6, 5))


def make_state() -> ProjectState:
    return ProjectState(
        project_id="P1",
        captured_at=CAPTURED_AT,
        project_name="Demo",
        tasks=[
            make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      total_slack_days=0.0, notes="one"),
            make_task("B", start=date(2026, 6, 2), finish=date(2026, 6, 3),
                      total_slack_days=1.0, notes="two"),
            make_task("DONE", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      total_slack_days=0.0, progress=1.0),
            make_task("SUMM", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      total_slack_days=0.0, is_summary=True),
            make_task("OUT", start=date(2026, 1, 1), finish=date(2026, 1, 2),
                      total_slack_days=0.0),
        ],
    )


def fake_loader(recorded: list | None = None):
    def loader():
        if recorded is not None:
            recorded.append(1)
        return make_state()

    return loader


def evaluated_everything(cd: float = 0.0):
    """A canned consequence evaluator: evaluates every session candidate."""
    from project_decision.application.consequence_evaluation import (
        ConsequenceEvaluation,
    )

    def evaluator(session, state):
        return tuple(
            ConsequenceEvaluation(
                task_id=task_id, status="evaluated", consequence_of_delay=cd
            )
            for task_id in session.candidate_task_ids
        )

    return evaluator


class TestLoadProject:
    def test_injected_loader_opens_the_workflow(self):
        calls: list[int] = []

        workflow = load_project(fake_loader(calls))

        assert calls == [1]  # the loader ran exactly once
        assert isinstance(workflow, DecisionWorkflowState)
        assert workflow.project_state.project_name == "Demo"
        assert workflow.planning_period is None
        assert workflow.planning_session is None
        assert workflow.confirmed is False

    def test_the_workflow_delegates_to_the_project_loader_function(self):
        from project_decision.application import project_loader

        state = make_state()

        class CannedAdapter:
            def read_active_project_state(self, captured_at=None):
                return state

        # Bind through the REAL loader function — the workflow does not
        # know (or care) how the ProjectState was produced.
        workflow = load_project(
            lambda: project_loader.load_project(
                CannedAdapter(), captured_at=CAPTURED_AT
            )
        )

        assert workflow.project_state.captured_at == CAPTURED_AT
        assert workflow.project_state.project_name == "Demo"


class TestPlanningPeriod:
    def test_valid_period_creates_a_session_from_default_discovery(self):
        state = make_state()
        workflow = load_project(fake_loader())

        updated = set_planning_period(workflow, WEEK)

        assert updated.planning_period == WEEK
        # Default candidates come from the EXISTING discovery function,
        # unchanged: finished and summary tasks excluded, OUT outside.
        assert updated.planning_session.planning_period == WEEK
        assert updated.planning_session.candidate_task_ids == (
            discover_default_candidates(state, WEEK)
        )
        assert updated.planning_session.candidate_task_ids == ("A", "B")
        assert updated.confirmed is False

    def test_replacing_the_period_replaces_the_session(self):
        workflow = set_planning_period(load_project(fake_loader()), WEEK)
        other = PlanningPeriod(start_date=date(2026, 6, 8), end_date=date(2026, 6, 12))

        updated = set_planning_period(workflow, other)

        assert updated.planning_period == other
        assert updated.planning_session.planning_period == other
        assert updated.planning_session is not workflow.planning_session

    def test_invalid_period_raises_the_domain_error(self):
        workflow = load_project(fake_loader())

        with pytest.raises(ValueError):
            set_planning_period(
                workflow,
                PlanningPeriod(start_date=date(2026, 6, 5), end_date=date(2026, 6, 1)),
            )


class TestCandidateEditing:
    def _prepared(self):
        return set_planning_period(load_project(fake_loader()), WEEK)

    def test_manual_candidate_is_appended_after_the_defaults(self):
        updated = add_candidate(self._prepared(), "OUT")

        assert updated.planning_session.candidate_task_ids == ("A", "B", "OUT")

    def test_unknown_task_is_rejected_by_the_existing_validation(self):
        with pytest.raises(ValueError, match="unknown task id"):
            add_candidate(self._prepared(), "NOPE")

    def test_duplicate_candidate_is_rejected(self):
        with pytest.raises(ValueError):
            add_candidate(self._prepared(), "A")

    def test_removing_a_candidate_updates_the_session(self):
        updated = remove_candidate(self._prepared(), "A")

        assert updated.planning_session.candidate_task_ids == ("B",)

    def test_removing_a_non_candidate_is_rejected(self):
        with pytest.raises(ValueError):
            remove_candidate(self._prepared(), "OUT")

    def test_removal_can_empty_the_session_but_never_the_state(self):
        state_at_start = make_state()
        workflow = load_project(lambda: state_at_start)
        workflow = set_planning_period(workflow, WEEK)

        workflow = remove_candidate(remove_candidate(workflow, "A"), "B")

        assert workflow.planning_session.candidate_task_ids == ()
        assert workflow.project_state.tasks  # the state keeps ALL tasks


class TestConfirmation:
    def test_confirmation_does_not_mutate_project_or_session(self):
        project_state = make_state()
        workflow = load_project(lambda: project_state)
        workflow = set_planning_period(workflow, WEEK)
        pristine_state = deepcopy(project_state)
        pristine_session = deepcopy(workflow.planning_session)

        confirmed = confirm_session(workflow)

        assert confirmed.confirmed is True
        assert confirmed.project_state == pristine_state
        assert confirmed.planning_session == pristine_session
        assert confirmed.planning_session is workflow.planning_session

    def test_editing_un_confirms(self):
        prepared = set_planning_period(load_project(fake_loader()), WEEK)
        workflow = confirm_session(prepared)

        edited = add_candidate(workflow, "OUT")

        assert workflow is not prepared            # confirm = new state
        assert workflow.confirmed is True          # done once, stays True
        assert edited.confirmed is False           # the edit un-confirms
        # and it does not touch the original confirmed state
        assert workflow.confirmed is (confirm_session(prepared).confirmed) is True

    def test_run_requires_a_session(self):
        with pytest.raises(ValueError, match="no planning session"):
            run_decision(load_project(fake_loader()), evaluated_everything())

    def test_run_requires_confirmation(self):
        workflow = set_planning_period(load_project(fake_loader()), WEEK)

        with pytest.raises(ValueError, match="not confirmed"):
            run_decision(workflow, evaluated_everything())


class TestRunDecision:
    def test_run_delegates_to_the_existing_pipeline(self):
        project_state = make_state()
        workflow = set_planning_period(
            load_project(lambda: project_state), WEEK
        )
        workflow = confirm_session(workflow)
        parameters = ScoringParameters(
            schedule_pressure_weight=0.5,
            successor_impact_weight=0.1,
            consequence_of_delay_weight=0.4,
        )

        result = run_decision(
            workflow, evaluated_everything(), scoring_parameters=parameters
        )

        expected = run_decision_pipeline(
            project_state,
            workflow.planning_session,
            evaluated_everything(),
            scoring_parameters=parameters,
        )
        assert result == expected  # identical to direct pipeline execution

    def test_evaluator_receives_session_and_state(self):
        seen: list[tuple] = []

        def evaluator(session, state):
            seen.append((session, state))
            return []

        workflow = confirm_session(set_planning_period(load_project(fake_loader()), WEEK))

        run_decision(workflow, evaluator)

        assert len(seen) == 1
        assert seen[0][0] is workflow.planning_session
        assert seen[0][1] == workflow.project_state

    def test_expected_evaluation_failures_stay_structured(self):
        from project_decision.application.consequence_evaluation import (
            STATUS_FAILED,
            ConsequenceEvaluation,
        )

        workflow = confirm_session(set_planning_period(load_project(fake_loader()), WEEK))

        def failing_evaluator(session, state):
            return tuple(
                ConsequenceEvaluation(
                    task_id=task_id, status=STATUS_FAILED,
                    review_reasons=("JEV evaluation failed: api down",),
                )
                for task_id in session.candidate_task_ids
            )

        result = run_decision(workflow, failing_evaluator)

        counts = result.decision_review.review_reason_counts
        assert counts["consequence_evaluation_failed"] == 2
        assert counts["provisional_consequence"] == 2


class TestResultInvalidation:
    def _decided(self):
        workflow = confirm_session(set_planning_period(load_project(fake_loader()), WEEK))
        return workflow, run_decision(workflow, evaluated_everything())

    def test_fresh_result_is_not_stale(self):
        workflow, result = self._decided()

        assert result_is_stale(workflow, result) is False

    def test_none_result_is_stale(self):
        workflow, _result = self._decided()

        assert result_is_stale(workflow, None) is True

    def test_candidate_changes_stale_the_result(self):
        workflow, result = self._decided()

        for edited in (add_candidate(workflow, "OUT"), remove_candidate(workflow, "A")):
            assert result_is_stale(edited, result) is True
            # ...and a re-run produces a valid, different decision.
            rerun = run_decision(confirm_session(edited), evaluated_everything())
            assert isinstance(rerun, DecisionPipelineResult)
            assert rerun != result  # no incremental patching of the old one

    def test_period_change_stales_the_result(self):
        workflow, result = self._decided()
        new_workflow = set_planning_period(
            workflow, PlanningPeriod(start_date=date(2026, 6, 8),
                                     end_date=date(2026, 6, 12))
        )

        assert result_is_stale(new_workflow, result) is True


class TestImmutabilityAndDeterminism:
    def test_transitions_never_mutate_the_workflow_state_or_project(self):
        project_state = make_state()
        pristine_state = deepcopy(project_state)
        workflow = set_planning_period(load_project(lambda: project_state), WEEK)
        pristine_workflow = deepcopy(workflow)
        confirmed = confirm_session(workflow)
        edited = add_candidate(confirmed, "OUT")

        assert project_state == pristine_state
        assert workflow == pristine_workflow  # original flow state untouched
        # The confirmed/edit steps created NEW states, each distinct.
        assert edited is not confirmed
        assert confirmed is not workflow
        assert "OUT" not in workflow.planning_session.candidate_task_ids

    def test_same_inputs_produce_the_same_workflow_and_result(self):
        def flow():
            workflow = set_planning_period(load_project(fake_loader()), WEEK)
            workflow = confirm_session(workflow)
            return workflow, run_decision(workflow, evaluated_everything())

        first_session, first_result = flow()
        second_session, second_result = flow()

        assert first_session == second_session
        assert first_result == second_result


class TestEmptyCandidateSession:
    def test_empty_session_confirms_and_runs_like_the_pipeline(self):
        state_at_start = make_state()
        workflow = load_project(lambda: state_at_start)
        workflow = set_planning_period(workflow, WEEK)
        workflow = remove_candidate(remove_candidate(workflow, "A"), "B")
        workflow = confirm_session(workflow)

        result = run_decision(workflow, evaluated_everything())

        assert result.decision_result.ranked_tasks == ()
        assert result.decision_review.status is ReviewStatus.READY  # valid, flag-free
        assert result.decision_review.candidate_count == 0
        assert result.decision_review.validation_issues == ()
        # Nothing fabricated: the structural stages still reflect the state.
        assert set(result.schedule_analysis.schedules) >= {"A", "B"}


class TestFullUiFlowIntegration:
    def test_load_period_edit_confirm_run_matches_direct_pipeline(self):
        from project_decision.application import project_loader
        from project_decision.domain import Resource

        state = ProjectState(
            project_id="P1",
            captured_at=CAPTURED_AT,
            project_name="Demo",
            tasks=[
                make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                          notes="critical path", total_slack_days=0.0,
                          assigned_resource_ids=("R1",)),
                make_task("B", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                          notes="cleanup", total_slack_days=4.0,
                          assigned_resource_ids=("R1",)),
                make_task("OUTSIDE", start=date(2026, 1, 1), finish=date(2026, 1, 2),
                          notes="manual pull-in", total_slack_days=0.0),
            ],
            resources=[Resource(id="R1", name="Alice")],
        )
        adapter_call_count: list[int] = []

        class CannedAdapter:
            def read_active_project_state(self, captured_at=None):
                adapter_call_count.append(1)
                return state

        # 1. Load → 2. Period → 3. Edit → 4. Confirm → 5. Run.
        workflow = load_project(
            lambda: project_loader.load_project(CannedAdapter(), captured_at=CAPTURED_AT)
        )
        workflow = set_planning_period(workflow, WEEK)          # defaults: A, B
        workflow = remove_candidate(workflow, "B")              # manager edit
        workflow = add_candidate(workflow, "OUTSIDE")           # manual pull-in
        assert workflow.planning_session.candidate_task_ids == ("A", "OUTSIDE")
        workflow = confirm_session(workflow)
        assert adapter_call_count == [1]  # loaded once, never re-loaded

        result = run_decision(workflow, evaluated_everything(cd=0.5))

        # Exactly equivalent to calling the existing pipeline directly on
        # the same final PlanningSession (same evaluator behavior).
        direct = run_decision_pipeline(
            state, workflow.planning_session, evaluated_everything(cd=0.5)
        )
        assert result == direct
        assert result.decision_review.candidate_count == 2
        assert result.decision_review.validation_issues == ()
        assert [r.task_id for r in result.decision_result.ranked_tasks] == ["A", "OUTSIDE"]
        # Resource warning follows A (R1 shared with B, which still exists
        # in the STATE even though B is not a candidate).
        assert result.decision_result.tasks_with_resource_warnings()[0].task_id == "A"
        assert result.decision_review.provisional_consequence_count == 0


class TestUiIndependence:
    def test_the_workflow_imports_nothing_forbidden(self):
        tree = ast.parse(inspect.getsource(workflow_module))
        modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        for module in modules:
            for forbidden in ("streamlit", "fastapi", "win32com",
                              "pythoncom", "typesafe"):
                assert forbidden not in module
