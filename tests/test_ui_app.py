"""Tests for the UI-adjacent helpers (fully offline, no browser launch).

The UI layer is thin by design, so these tests pin the few things it
MUST guarantee:
1. formatting helpers never fabricate or alter values (None → "—");
2. status/badge mapping uses the existing review statuses only;
3. the run guard and stale-result handling call the WORKFLOW's own
   functions (no decision rules live in the UI);
4. result→table transformations consume real pipeline data verbatim;
5. import hygiene: the UI touches only the workflow boundary, display
   models, and configuration — never MSP COM, the TypeSafe SDK, DB
   libraries, or pipeline/scoring/ranking/review implementations.
"""

import ast
import pathlib
from datetime import date, datetime

import pytest

from project_decision.application.consequence_evaluation import ConsequenceEvaluation
from project_decision.application.decision_review import (
    DecisionReview,
    ReviewStatus,
    review_decision_result,
)
from project_decision.application.decision_pipeline import run_decision_pipeline
from project_decision.application.decision_workflow import (
    add_candidate,
    confirm_session,
    load_project,
    run_decision,
    set_planning_period,
)
from project_decision.application.priority_ranking import RankedTask
from project_decision.application.priority_scoring import ScoringParameters
from project_decision.domain import ProjectState, Resource
from project_decision.domain.planning_period import PlanningPeriod
from project_decision.domain.planning_session import PlanningSession
from tests.conftest import make_task

from app.ui import components

CAPTURED_AT = datetime(2026, 10, 21, 9, 0)
WEEK = PlanningPeriod(start_date=date(2026, 6, 1), end_date=date(2026, 6, 5))


def make_state() -> ProjectState:
    """A, B are default candidates; C is finished (never a candidate)."""
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
            make_task("C", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                      progress=1.0, notes="done"),
        ],
        resources=[Resource(id="R1", name="Alice")],
    )


def offline_evaluator(session, state):
    """The NOTHING-evaluating evaluator the offline UI injects."""
    return None


def make_workflow():
    workflow = load_project(lambda: make_state())
    return set_planning_period(workflow, WEEK)


def make_pipeline_result(evaluations=None):
    """A real offline pipeline result, exactly as the built UI stores it."""
    workflow = make_workflow()
    session = workflow.planning_session
    if evaluations is None:
        evaluator = offline_evaluator
    else:
        def evaluator(session_arg, state_arg):
            return evaluations
    return workflow, run_decision_pipeline(workflow.project_state, session, evaluator)


# -- formatting helpers: values are shown, never altered -------------------------


class TestFormatting:
    def test_score_one_decimal(self):
        assert components.score_text(74.56) == "74.6"
        assert components.score_text(80) == "80.0"

    def test_score_none_is_not_a_number(self):
        assert components.score_text(None) == "—"

    def test_criterion_as_percentage(self):
        assert components.criterion_text(0.671) == "67%"
        assert components.criterion_text(1.0) == "100%"

    def test_slack_uses_days_and_keeps_negative(self):
        assert components.slack_text(-2.0) == "-2.0 d"
        assert components.slack_text(0.0) == "0.0 d"
        assert components.slack_text(None) == "—"

    def test_optional_text(self):
        assert components.optional_text(None) == "—"
        assert components.optional_text("R1") == "R1"


# -- status mapping / guards -----------------------------------------------------


class TestStatusAndGuards:
    def test_every_review_status_has_a_badge(self):
        for status in ReviewStatus:
            label, css_class = components.status_badge(status)
            assert css_class in ("ready", "review", "invalid")

    def test_run_guard_step_by_step(self):
        workflow = load_project(lambda: make_state())
        runnable, reason = components.can_run_decision(workflow)
        assert (runnable, reason) == (False, "Select a planning period first.")

        workflow = set_planning_period(workflow, WEEK)
        runnable, reason = components.can_run_decision(workflow)
        assert (runnable, reason) == (False, "Confirm the planning session first.")

        runnable, reason = components.can_run_decision(confirm_session(workflow))
        assert runnable is True and reason is None

    def test_none_workflow_never_runnable(self):
        assert components.can_run_decision(None)[0] is False


class TestStaleResultHandling:
    def test_no_result_is_reported_stale(self):
        workflow = make_workflow()
        result, message = components.clear_if_stale(None, workflow)
        assert result is None
        assert message is not None

    def test_result_for_previous_session_is_cleared(self):
        workflow = make_workflow()
        old_result = run_decision_pipeline(
            workflow.project_state,
            workflow.planning_session,
            offline_evaluator,
        )
        # A period edit produces a NEW session in the workflow state.
        edited = set_planning_period(
            workflow, PlanningPeriod(date(2026, 6, 8), date(2026, 6, 12))
        )
        result, message = components.clear_if_stale(old_result, edited)
        assert result is None and message is not None

    def test_result_for_current_session_is_kept(self):
        workflow = confirm_session(make_workflow())
        result = run_decision(workflow, offline_evaluator)
        kept, message = components.clear_if_stale(result, workflow)
        assert kept is result and message is None


# -- result → display transformations (real pipeline data) ------------------------


class TestTableTransformations:
    def test_ranking_rows_follow_rank_order_and_formatting(self):
        _workflow, result = make_pipeline_result()
        rows = components.ranking_rows(result)
        assert [row["Rank"] for row in rows] == [1, 2]
        assert rows[0]["Activity"] == "Task A (A)"
        assert rows[0]["Score"] == components.score_text(
            result.decision_result.ranked_tasks[0].total_score
        )
        assert rows[0]["Status"] in ("OK", "REVIEW", "RESOURCE WARNING", "UNSCORED")

    def test_row_states_are_presentation_compositions_of_existing_flags(self):
        from project_decision.application.priority_scoring import TaskPriorityScore

        def scored(**overrides):
            fields = dict(
                task_id="A", schedule_pressure=0.5, successor_impact=0.5,
                consequence_of_delay=0.5, consequence_source="provisional",
                schedule_contribution=0.2, successor_contribution=0.1,
                consequence_contribution=0.2, total_score=50.0,
                needs_review=True, review_reasons=("provisional",),
            )
            return TaskPriorityScore(**{**fields, **overrides})

        def ranked(score, resource_warning=False):
            return RankedTask(
                rank=1, task_id="A", task_name="Task A", score=score,
                total_slack_days=0.0, successor_count=1, is_critical=False,
                resource_warning=resource_warning,
            )

        assert components.row_state(ranked(scored(
            needs_review=True, review_reasons=("provisional",),
        ))) == "REVIEW"
        assert components.row_state(
            ranked(scored(needs_review=True, review_reasons=("provisional",),
                          consequence_source="evaluated"), resource_warning=True)
        ) == "RESOURCE WARNING"
        assert components.row_state(ranked(scored(
            needs_review=False, review_reasons=(),
            consequence_source="evaluated",
        ))) == "OK"

    def test_unscored_and_missing_values_are_shown_honestly(self):
        """A candidate with NO score at all: ranked last, no fabricated 0."""
        from types import SimpleNamespace

        from project_decision.application.priority_ranking import (
            create_decision_result,
        )
        from project_decision.application.resource_conflicts import (
            analyze_resource_conflicts,
        )
        from project_decision.application.schedule_analysis import analyze_schedule

        workflow = make_workflow()
        state = workflow.project_state
        decision_result = create_decision_result(
            workflow.planning_session, state, analyze_schedule(state),
            scores=[], conflict_analysis=analyze_resource_conflicts(state),
        )
        rows = components.ranking_rows(
            SimpleNamespace(decision_result=decision_result)
        )
        last = rows[-1]
        assert last["Rank"] == 2  # two candidates, both unscored → ranks 1..2
        assert last["Review"] == "unscored"
        assert last["Status"] == "UNSCORED"
        assert last["Score"] == "—"
        assert last["Schedule Pressure"] == "—"
        assert last["Successor Impact"] == "—"
        assert last["Resource"] == "—"

    def test_contribution_rows_use_the_parameters_the_run_used(self):
        _workflow, result = make_pipeline_result()
        ranked = result.decision_result.ranked_tasks[0]
        rows = components.contribution_rows(ranked, ScoringParameters())
        parameters = ScoringParameters()
        assert [row["Criterion"] for row in rows] == [
            "Schedule Pressure", "Successor Impact", "Consequence of Delay",
        ]
        assert rows[0]["Weight"] == f"{parameters.schedule_pressure_weight:.2f}"
        assert rows[2]["Weight"] == f"{parameters.consequence_of_delay_weight:.2f}"
        # Contributions are exactly the stored ones, unmodified.
        for row, contribution in zip(rows, ranked.criterion_contributions):
            assert row["Contribution"] == components.score_text(contribution)

    def test_evaluation_details_without_evaluations_is_none(self):
        _workflow, result = make_pipeline_result()  # offline evaluator
        assert result.consequence_evaluations is None
        assert components.evaluation_details(result, "A") is None

    def test_evaluation_details_reads_stored_evaluation(self):
        evaluations = [
            ConsequenceEvaluation(
                task_id="A", status="evaluated", consequence_of_delay=1.0,
                jev_score=3.0, confidence=0.8,
                probabilities={0: 0.1, 3: 0.9},
                legend={0: "no impact", 3: "stop of operations"},
            ),
            ConsequenceEvaluation(task_id="B", status="unavailable"),
        ]
        _workflow, result = make_pipeline_result(evaluations=evaluations)
        details = components.evaluation_details(result, "A")
        assert details["status"] == "evaluated"
        assert details["confidence"] == 0.8
        assert details["probabilities"] == {0: 0.1, 3: 0.9}
        assert details["legend"] == {0: "no impact", 3: "stop of operations"}
        assert components.evaluation_details(result, "GX") is None

    def test_conflict_details_come_from_existing_conflict_analysis(self):
        # A and B both own "Alice" over the same window → one CONFLICT pair.
        state = ProjectState(
            project_id="P1", captured_at=CAPTURED_AT, project_name="Demo",
            tasks=[
                make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                          notes="permit needed", total_slack_days=0.0,
                          assigned_resource_ids=("R1",)),
                make_task("B", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                          notes="cleanup", total_slack_days=3.0,
                          assigned_resource_ids=("R1",)),
            ],
            resources=[Resource(id="R1", name="Alice")],
        )
        workflow = set_planning_period(load_project(lambda: state), WEEK)
        result = run_decision_pipeline(
            workflow.project_state, workflow.planning_session, offline_evaluator
        )
        rows = components.conflict_details(result, "A")
        assert len(rows) == 1
        assert rows[0]["Other task"] == "B"
        assert rows[0]["Shared resources"] == "Alice"
        assert rows[0]["Overlap"] != "not determined"
        assert components.conflict_details(result, "GX") == []

    def test_review_chips_are_machine_counts_only(self):
        review = DecisionReview(
            status=ReviewStatus.REVIEW_REQUIRED,
            validation_issues=(),
            candidate_count=2,
            ranked_count=2,
            tasks_needing_review=("A",),
            review_reason_counts={"provisional_consequence": 2},
            resource_warning_count=0,
            tasks_with_resource_warnings=(),
            missing_schedule_count=0,
            tasks_with_missing_schedule=(),
            provisional_consequence_count=2,
            low_confidence_consequence_count=0,
            unscored_task_ids=(),
        )
        assert components.review_reason_chips(review) == [
            "provisional consequence: 2"
        ]


# -- period suggestion / candidate labeling --------------------------------------


class TestPeriodAndCandidates:
    def test_default_bounds_from_project_dates(self):
        state = make_state()
        assert components.default_period_bounds(state) == (
            date(2026, 6, 1), date(2026, 6, 2)
        )

    def test_default_bounds_without_any_dates(self):
        state = ProjectState(
            project_id="P1", captured_at=CAPTURED_AT, project_name="D",
            tasks=[
                make_task("A", start=None, finish=None),
                make_task("B", start=None, finish=None),
            ],
        )
        assert components.default_period_bounds(state) == (None, None)

    def test_candidate_rows_label_default_and_manual(self):
        workflow = make_workflow()  # defaults A, B; C excluded (finished)
        rows = components.candidate_rows(
            workflow.project_state, workflow.planning_session
        )
        assert [row["Source"] for row in rows] == ["discovered", "discovered"]

        manual = add_candidate(workflow, "C")
        rows = components.candidate_rows(
            manual.project_state, manual.planning_session
        )
        assert rows[-1]["Task ID"] == "C" and rows[-1]["Source"] == "manually added"

    def test_candidate_row_values_match_the_domain_task(self):
        workflow = make_workflow()
        rows = components.candidate_rows(
            workflow.project_state, workflow.planning_session
        )
        assert rows[0]["Name"] == "Task A"
        assert rows[0]["Slack"] == "0.0 d"
        assert rows[0]["Progress"] == "0%"


# -- import hygiene (the UI stays thin, offline, read-only) ----------------------


APP_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"

FORBIDDEN_LIBRARY_PREFIXES = (
    "win32com", "win32api", "win32lib", "pythoncom", "pywintypes",
    "typesafe_sdk", "fastapi", "sqlalchemy",
)

# Application pipeline functions the UI must never import or call:
# everything flows through the workflow functions instead.
FORBIDDEN_NAMES = frozenset({
    "run_decision_pipeline",
    "score_session_with_evaluations",
    "create_decision_result",
    "review_decision_result",
    "validate_decision_result",
    "analyze_schedule",
    "analyze_resource_conflicts",
})

# Modules the UI may import from the project package (fully enumerated;
# anything else fails and must be added consciously).
ALLOWED_MODULES = {
    # the one application boundary the UI talks to
    "project_decision.application.decision_workflow",
    # display-only models, statuses, and weights (no functions called)
    "project_decision.application.decision_pipeline",
    "project_decision.application.decision_review",
    "project_decision.application.priority_ranking",
    "project_decision.application.priority_scoring",
    "project_decision.application.resource_conflicts",
    # the JEV evaluator facade (JEV is offline-skipped by the UI)
    "project_decision.application.consequence_evaluation",
    # read-only connectors and read-only display reuse
    "project_decision.application.project_loader",
    "project_decision.application.candidate_discovery",
    # configuration + error type (no client code)
    "project_decision.config",
    "project_decision.infrastructure.msp.exceptions",
    # Demo Mode: the offline fixture enters through the SAME workflow
    # boundary as the MSP loader (a ProjectLoader, not a pipeline call).
    "project_decision.demo",
}

MSP_WRITE_HINTS = ("SaveAs", "FileSave", "FileClose", ".Save(")


def app_files():
    return sorted(APP_ROOT.rglob("*.py"))


def test_ui_source_files_exist():
    assert {path.name for path in app_files()} >= {
        "streamlit_app.py", "views.py", "components.py", "styles.py", "charts.py",
    }


def test_no_forbidden_libraries_inside_the_ui():
    for path in app_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        for name in modules:
            assert not name.startswith(FORBIDDEN_LIBRARY_PREFIXES), (
                f"{path.name} imports forbidden library {name}"
            )


def test_ui_never_imports_or_calls_pipeline_decision_functions():
    for path in app_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    assert alias.name.split(".")[-1] not in FORBIDDEN_NAMES, (
                        f"{path.name} imports decision function {alias.name}"
                    )
                    assert alias.asname != "score", "aliasing hides the rule"
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in FORBIDDEN_NAMES, (
                    f"{path.name} calls decision function {node.func.id}"
                )


def test_ui_project_imports_are_fully_enumerated():
    for path in app_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "project_decision.domain" or (
                    node.module.startswith("project_decision.domain.")
                ):
                    continue  # domain models are display-only data
                if node.module.startswith("project_decision"):
                    assert node.module in ALLOWED_MODULES, (
                        f"{path.name} imports unexpected module {node.module}"
                    )
                    if node.module == "project_decision.infrastructure.msp.exceptions":
                        allowed_error_names = {
                            "MspError", "MspNotAvailableError",
                            "MspNoActiveProjectError", "MspReadError",
                            "MspMappingError",
                        }
                        assert all(
                            alias.name in allowed_error_names for alias in node.names
                        )
                    continue  # this project's package is decided above
                if node.module.startswith("app.ui") or node.module == "app.ui":
                    continue
                # third-party / stdlib — only the expected handful
                assert node.module.split(".")[0] in {
                    "__future__", "streamlit", "plotly", "datetime",
                    "pathlib", "sys", "collections", "dataclasses",
                    "functools", "typing",
                }, f"{path.name} imports unexpected module {node.module}"


def test_no_msp_write_operations_inside_the_ui():
    for path in app_files():
        source = path.read_text(encoding="utf-8")
        for hint in MSP_WRITE_HINTS:
            assert hint not in source, (
                f"{path.name} mentions an MSP write/save call: {hint}"
            )


def test_no_top_level_streamlit_widget_calls_so_imports_stay_offline():
    for path in app_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                func = node.value.func
                if isinstance(func, ast.Attribute) and isinstance(
                    func.value, ast.Name
                ):
                    assert func.value.id != "st", (
                        f"{path.name} runs st.{func.attr} at import time"
                    )


def test_entry_module_import_works_offline_without_msp_or_rendering():
    import importlib

    module = importlib.import_module("app.streamlit_app")
    assert callable(module.main)
    views = importlib.import_module("app.ui.views")
    assert views.WORKFLOW_KEY == "decision_workflow"
    assert views.RESULT_KEY == "decision_pipeline_result"


# -- Streamlit AppTest smoke tests (in-memory, no server, no COM) ---------------


class TestStreamlitAppSmoke:
    """The real script runs in-memory; MSP loading is patched at its module."""

    APP_PATH = pathlib.Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"

    @pytest.fixture(autouse=True)
    def _no_api_key(self, monkeypatch):
        """JEV must stay unconfigured for offline smoke tests."""
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    def _run(self):
        from streamlit.testing.v1 import AppTest

        return AppTest.from_file(self.APP_PATH, default_timeout=20).run()

    def test_initial_page_renders_setup_only(self):
        at = self._run()
        assert not at.exception
        markdowns = [markdown.value for markdown in at.markdown]
        assert any("Project Decision Assistant" in value for value in markdowns)
        buttons = [button.label for button in at.button]
        assert "Load Active MSP Project" in buttons
        # No dashboard content before anything is done.
        assert "Decision Dashboard" not in [
            header.value for header in at.subheader
        ]

    def test_load_failure_shows_error_and_keeps_setup(self, monkeypatch):
        from project_decision.application import project_loader
        from project_decision.infrastructure.msp.exceptions import (
            MspNoActiveProjectError,
        )

        monkeypatch.setattr(
            project_loader,
            "load_active_project",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                MspNoActiveProjectError("no active project")
            ),
        )
        at = self._run()
        assert not at.exception
        # Click "Load Active MSP Project" (by label — the demo button
        # now shares the row) and re-run the script in-memory.
        self._click(at, "Load Active MSP Project")
        assert not at.exception
        assert len(at.error) == 1
        assert "no project is active" in at.error[0].value

    def _click(self, at, label):
        matches = [button for button in at.button if button.label == label]
        assert matches, f"button {label!r} not found"
        matches[0].click().run()

    def test_load_success_shows_project_summaries(self, monkeypatch):
        from project_decision.application import project_loader

        monkeypatch.setattr(
            project_loader, "load_active_project", lambda *args, **kwargs: make_state()
        )
        at = self._run()
        self._click(at, "Load Active MSP Project")  # live loader (patched)
        assert not at.exception
        # The project facts panel renders and the period step opens up.
        subheaders = [header.value for header in at.subheader]
        assert "Project" in subheaders
        assert "Planning Period" in subheaders

    def test_full_offline_flow_renders_the_ranked_dashboard(self, monkeypatch):
        from project_decision.application import project_loader

        monkeypatch.setattr(
            project_loader, "load_active_project", lambda *args, **kwargs: make_state()
        )
        at = self._run()
        self._click(at, "Load Active MSP Project")

        # Period: defaults come from the project dates; apply them.
        start, finish = date(2026, 6, 1), date(2026, 6, 5)
        at.date_input[0].set_value((start, finish)).run()
        self._click(at, "Apply planning period")
        assert "Default candidates" in [metric.label for metric in at.metric]

        # Confirm, then run (JEV sidebar radio stays OFFLINE by default).
        self._click(at, "Confirm Planning Session")
        self._click(at, "Run Decision Analysis")

        assert not at.exception
        subheaders = [header.value for header in at.subheader]
        assert "Decision Dashboard" in subheaders
        markdowns = [markdown.value for markdown in at.markdown]
        assert any("Priority Ranking" in value for value in markdowns)
        # Offline: every consequence is provisional → review required.
        assert any("REVIEW REQUIRED" in value for value in markdowns)

    def test_demo_flow_reaches_the_dashboard_without_any_service(self):
        """"Demo Project" runs the FULL flow offline: no MSP, no COM,
        no JEV, no key, no network — the easiest path for a new user."""
        at = self._run()
        self._click(at, "Load Demo Project")
        assert not at.exception
        metrics = {metric.label: metric.value for metric in at.metric}
        assert "(Demo)" in metrics["Project"]

        # The suggested period accepts the fixture's own date range.
        self._click(at, "Apply planning period")
        metrics = {metric.label: metric.value for metric in at.metric}
        assert int(metrics["Default candidates"]) >= 6

        self._click(at, "Confirm Planning Session")
        self._click(at, "Run Decision Analysis")
        assert not at.exception
        subheaders = [header.value for header in at.subheader]
        assert "Decision Dashboard" in subheaders
        assert any("Priority Ranking" in m.value for m in at.markdown)
        assert any("REVIEW REQUIRED" in m.value for m in at.markdown)

    def test_invalid_parameters_clear_the_dashboard_with_a_notice(self):
        """Invalid weights must never leave a result displayed next to them."""
        at = self._run()
        self._click(at, "Load Demo Project")
        self._click(at, "Apply planning period")
        self._click(at, "Confirm Planning Session")
        self._click(at, "Run Decision Analysis")
        assert "Decision Dashboard" in [h.value for h in at.subheader]

        # 0.50 + 0.20 + 0.40 = 1.10: the model rejects these parameters.
        at.number_input[0].set_value(0.50).run()

        assert not at.exception
        assert "Decision Dashboard" not in [h.value for h in at.subheader]
        assert any(
            "invalid" in warning.value.lower()
            and "cleared" in warning.value.lower()
            for warning in at.warning
        )

    def test_fresh_period_apply_does_not_claim_a_cleared_result(self):
        """Regression: with nothing run yet there IS no previous result —
        the UI must not say one was cleared."""
        at = self._run()
        self._click(at, "Load Demo Project")
        self._click(at, "Apply planning period")
        infos = [info.value for info in at.info]
        assert not any("previous result was cleared" in value for value in infos)

    def test_new_cycle_resets_back_to_the_source_choice(self):
        at = self._run()
        self._click(at, "Load Demo Project")
        self._click(at, "Start a new planning cycle")  # inside the expander
        buttons = [button.label for button in at.button]
        assert "Load Demo Project" in buttons
        assert "Load Active MSP Project" in buttons
        # back to setup: no project facts panel anymore
        assert "Planning Period" not in [
            header.value for header in at.subheader
        ]



# -- Step 13: product hardening helpers ------------------------------------------


class TestWorkflowStepper:
    def test_steps_progress_state_by_state(self):
        states = components.workflow_steps(None, has_result=False)
        assert [state for _, state in states] == [
            "active", "todo", "todo", "todo", "todo",
        ]

        workflow = set_planning_period(load_project(lambda: make_state()), WEEK)
        states = components.workflow_steps(workflow, has_result=False)
        labels = dict(states)
        assert labels["Project"] == "done"
        assert labels["Planning Period"] == "done"
        assert labels["Candidate Activities"] == "done"
        assert labels["Decision Setup"] == "active"

        confirmed = confirm_session(workflow)
        result = run_decision(confirmed, offline_evaluator)
        states = components.workflow_steps(confirmed, has_result=True)
        assert [state for _, state in states] == ["done"] * 5


class TestStatusExplanations:
    def test_every_review_status_has_an_explanation(self):
        for status in ReviewStatus:
            explanation = components.status_explanation(status)
            assert explanation  # non-empty presentation text


class TestParameterEditor:
    def test_defaults_build_valid_parameters(self):
        parameters, note = components.build_scoring_parameters(
            0.40, 0.20, 0.40, 5, None
        )
        assert note is None
        assert parameters == ScoringParameters()

    def test_invalid_weights_rejected_with_the_model_message(self):
        parameters, note = components.build_scoring_parameters(
            0.5, 0.2, 0.4, 5, None  # sums to 1.1
        )
        assert parameters is None
        assert "sum to 1.0" in note  # existing validation, verbatim

    def test_non_normalizing_cap_and_horizon(self):
        parameters, note = components.build_scoring_parameters(
            0.4, 0.2, 0.4, 0, None  # K < 1 → the model rejects it
        )
        assert parameters is None and "successor_cap" in note


class TestParameterStaleness:
    def test_matching_parameters_keep_the_result(self):
        changed, message = components.clear_if_parameters_changed(
            ScoringParameters(), ScoringParameters()
        )
        assert changed is False and message is None

    def test_changed_parameters_require_a_rerun(self):
        changed, message = components.clear_if_parameters_changed(
            ScoringParameters(),
            ScoringParameters(schedule_pressure_weight=0.6,
                              successor_impact_weight=0.2,
                              consequence_of_delay_weight=0.2),
        )
        assert changed is True
        assert "Run the decision analysis again" in message

    def test_no_stored_parameters_never_blocks(self):
        changed, _ = components.clear_if_parameters_changed(None, ScoringParameters())
        assert changed is False

    def test_invalid_parameters_clear_a_stored_result(self):
        _workflow, result = make_pipeline_result()

        cleared, message = components.clear_if_parameters_invalid(result, None)

        assert cleared is None
        assert "invalid" in message.lower()
        assert "cleared" in message.lower()

    def test_invalid_parameters_without_a_result_are_silent(self):
        assert components.clear_if_parameters_invalid(None, None) == (None, None)

    def test_valid_parameters_keep_the_result(self):
        _workflow, result = make_pipeline_result()

        kept, message = components.clear_if_parameters_invalid(
            result, ScoringParameters()
        )

        assert kept is result and message is None


class TestTaskDetailReviewReasons:
    def test_unscored_task_is_never_reported_clean(self):
        ranked = RankedTask(
            rank=1, task_id="A", task_name="A", score=None,
            total_slack_days=None, successor_count=None, is_critical=None,
            resource_warning=False,
        )

        reasons = components.detail_review_reasons(ranked)

        assert reasons == (components.UNSCORED_DETAIL_REASON,)
        assert "No review issues" not in reasons[0]
        assert "unscored" in reasons[0].lower()

    def test_scored_task_uses_its_existing_review_reasons(self):
        _workflow, result = make_pipeline_result()  # offline -> provisional
        ranked = result.decision_result.ranked_tasks[0]

        assert components.detail_review_reasons(ranked) == ranked.review_reasons


class TestProjectSummary:
    def test_summary_rows_use_captured_values(self):
        workflow = load_project(lambda: ProjectState(
            project_id="P1", captured_at=CAPTURED_AT, project_name="Demo",
            tasks=[make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2))],
        ))
        rows = components.project_summary_rows(workflow)
        assert rows[0] == {"Fact": "Project name", "Value": "Demo"}
        assert rows[1]["Value"] == "1"  # task count as display text
        assert rows[2]["Value"] == "0"  # no resources
        assert rows[3]["Value"] == "2026-06-01 to 2026-06-02"
        assert rows[4]["Value"] == "2026-10-21 09:00"

    def test_missing_project_name_is_never_fabricated(self):
        workflow = load_project(lambda: ProjectState(
            project_id="P", captured_at=CAPTURED_AT, project_name=None,
            tasks=[make_task("A")],
        ))
        rows = components.project_summary_rows(workflow)
        assert rows[0]["Value"] == "—"


class TestSuccessorDetails:
    def test_successor_ids_and_names_come_from_the_stored_analysis(self):
        state = ProjectState(
            project_id="P1", captured_at=CAPTURED_AT, project_name="Demo",
            tasks=[
                make_task("A", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                          notes="n", total_slack_days=0.0),
                make_task("B", start=date(2026, 6, 1), finish=date(2026, 6, 2),
                          dependency_ids=("A",), notes="n"),
            ],
        )
        workflow = set_planning_period(load_project(lambda: state), WEEK)
        result = run_decision_pipeline(
            workflow.project_state, workflow.planning_session, offline_evaluator
        )
        details = components.successor_details(result, state, "A")
        assert details["successor_ids"] == ("B",)
        assert details["successor_names"] == ("Task B",)
        assert details["successor_count"] == 1


class TestReviewCenter:
    def test_rows_only_from_existing_review_fields(self):
        _workflow, result = make_pipeline_result()  # offline → provisional
        rows = components.review_center_rows(result)
        issues = {row["Issue"] for row in rows}
        assert "Provisional consequence" in issues
        assert "Missing schedule data" not in issues  # schedule exists here

    def test_failed_evaluations_are_reported_from_the_result(self):
        evaluations = [
            ConsequenceEvaluation(task_id="A", status="unavailable"),
            ConsequenceEvaluation(
                task_id="B", status="failed", error_kind="api",
                error_message="boom",
            ),
        ]
        _workflow, result = make_pipeline_result(evaluations=evaluations)
        issues = {row["Issue"] for row in components.review_center_rows(result)}
        assert "JEV evaluation failed / unavailable" in issues

    def test_clean_review_yields_no_rows(self):
        workflow = confirm_session(make_workflow())
        result = run_decision(workflow, _all_evaluated(("A", "B")))
        # Every candidate evaluated with a definite consequence → no
        # provisional reasons; nothing else fails → the review is clean.
        rows = components.review_center_rows(result)
        assert rows == []


class TestCharts:
    def test_ranking_figure_plots_rank_order_scores_only(self):
        from app.ui import charts

        _workflow, result = make_pipeline_result()
        ranked = result.decision_result.ranked_tasks
        figure, note = charts.ranking_figure(ranked)
        assert note is None
        bars = figure.data[0]
        assert list(bars.y) == [
            one.total_score for one in ranked if one.total_score is not None
        ]

    def test_ranking_figure_truncates_many_and_notes_it(self):
        from app.ui import charts

        ranked = tuple(
            RankedTask(rank=i + 1, task_id=f"T{i}", task_name=f"Task {i}",
                       score=None, total_slack_days=0.0, successor_count=0,
                       is_critical=None, resource_warning=False)
            for i in range(20)
        )
        assert charts.ranking_figure(ranked)[0] is None  # nothing scored
        assert charts.ranking_note(ranked) is None
        scored = tuple(
            RankedTask(rank=i + 1, task_id=f"T{i}", task_name=f"Task {i}",
                       score=_score(10.0), total_slack_days=0.0,
                       successor_count=0, is_critical=None,
                       resource_warning=False)
            for i in range(20)
        )
        figure, _ = charts.ranking_figure(scored)
        assert len(figure.data[0].x) == charts.MAX_RANKING_BARS
        assert "top" in charts.ranking_note(scored)

    def test_contribution_figure_uses_stored_contributions(self):
        from app.ui import charts

        _workflow, result = make_pipeline_result()
        ranked = result.decision_result.ranked_tasks[0]
        figure = charts.contribution_figure(ranked)
        assert list(figure.data[0].y) == [
            value for value in ranked.criterion_contributions
            if value is not None
        ]

    def test_unscored_activity_has_no_contribution_figure(self):
        from app.ui import charts

        empty_score = RankedTask(
            rank=1, task_id="A", task_name="A", score=None,
            total_slack_days=None, successor_count=None, is_critical=None,
            resource_warning=False,
        )
        assert charts.contribution_figure(empty_score) is None


def _score(value):
    from project_decision.application.priority_scoring import TaskPriorityScore

    return TaskPriorityScore(
        task_id="T", schedule_pressure=0.5, successor_impact=0.5,
        consequence_of_delay=0.5, consequence_source="evaluated",
        schedule_contribution=0.1, successor_contribution=0.1,
        consequence_contribution=0.2, total_score=value,
        needs_review=False, review_reasons=(),
    )


def _all_evaluated(task_ids):
    """A canned evaluator giving every candidate CD=1 (definite values)."""
    from project_decision.application.consequence_evaluation import ConsequenceEvaluation

    def evaluator(session, state_arg):
        del state_arg
        return [
            ConsequenceEvaluation(
                task_id=task_id, status="evaluated", consequence_of_delay=1.0,
            ) for task_id in task_ids
        ]

    return evaluator
