"""Hermetic tests for the terminal UI (no COM, no network, no Microsoft
Project, no real JEV service).

Guards:
1. the bridge forwards to the EXISTING application layer and never
   invents evaluator behavior (OFFLINE = nothing evaluated; LIVE without
   a configured key refuses loudly);
2. error text stays structured (WHAT/WHY/NEXT), never a stack trace;
3. the App renders the required states honestly: disconnected, loaded
   (CONNECTED only after a real read), error, and analyzed (real ranked
   rows + honest provenance labels);
4. the JEV mode toggle can NEVER silently pretend to be live: without a
   configured key it stays OFFLINE, with one it switches;
5. keyboard flow: [P] opens the period modal (cancel/apply/validate),
   [C] confirm, [A] analyze, [J] JEV mode;
6. display widgets render real values and explicit "no data" bars.

"""
from __future__ import annotations

import datetime as dt
import pathlib

import pytest
from textual.widgets import Input

from project_decision.application.decision_review import ReviewStatus
from project_decision.application.decision_workflow import (
    confirm_session,
    load_project,
    run_decision,
    set_planning_period,
)
from project_decision.domain import PlanningPeriod, ProjectState, Resource, Task
from project_decision.infrastructure.msp.exceptions import MspNotAvailableError
from project_decision.tui import data as bridge
from project_decision.tui.app import DecisionTUI, PeriodScreen

CAPTURED = dt.datetime(2026, 10, 21, 10, 0)
PERIOD = PlanningPeriod(dt.date(2026, 10, 22), dt.date(2026, 10, 29))


def render(widget) -> str:
    return str(widget.render())


def make_state() -> ProjectState:
    """A REAL, small ProjectState: shared resource, mixed progress."""
    tasks = [
        Task(
            id="T1",
            name="Roofing & cladding",
            duration_days=6.0,
            progress=0.3,
            start=dt.date(2026, 10, 22),
            finish=dt.date(2026, 10, 29),
            assigned_resource_ids=["R1"],
            total_slack_days=0.0,
            is_critical=True,
            notes="a late finish breaches the weather-window contract",
        ),
        Task(
            id="T2",
            name="Cable trays & containment",
            duration_days=3.0,
            progress=0.0,
            start=dt.date(2026, 10, 23),
            finish=dt.date(2026, 10, 27),
            assigned_resource_ids=["R1"],
            total_slack_days=2.0,
            is_critical=False,
        ),
        Task(
            id="T3",
            name="Operator training",
            duration_days=2.0,
            progress=0.0,
            start=dt.date(2026, 10, 27),
            finish=dt.date(2026, 10, 29),
            assigned_resource_ids=["R2"],
            total_slack_days=5.0,
            is_critical=False,
        ),
    ]
    return ProjectState(
        project_id="SAMPLE Test Facility.mpp",
        captured_at=CAPTURED,
        project_name="Test Facility Commissioning",
        tasks=tasks,
        resources=[
            Resource(id="R1", name="Electrician"),
            Resource(id="R2", name="Trainer"),
        ],
        status_date=dt.date(2026, 10, 21),
    )


def confirmed_workflow(state=None):
    """load → period → confirm, all through the EXISTING functions."""
    state = state if state is not None else make_state()
    workflow = set_planning_period(load_project(lambda: state), PERIOD)
    return confirm_session(workflow)


class TestBridge:
    def test_sample_path_points_at_the_bundled_mpp(self):
        path = bridge.sample_project_path()
        assert path.endswith("Production_Line_Commissioning.mpp")
        assert pathlib.Path(path).is_file()

    def test_sample_guide_is_operational_and_honest(self):
        text = bridge.sample_project_guide_text()
        for key in ("[L]", "[P]", "[C]", "[A]", "[J]"):
            assert key in text
        assert "read-only" in text.lower()
        assert "provisional" in text.lower()
        assert "TYPESAFE_API_KEY" in text
        assert "examples/production-line-commissioning/" in text

    def test_offline_evaluator_evaluates_nothing(self):
        state = make_state()
        session = set_planning_period(
            load_project(lambda: state), PERIOD
        ).planning_session
        assert bridge.offline_evaluator()(session, state) is None

    def test_live_mode_without_a_key_refuses_loudly(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        evaluator, note = bridge.evaluator_for_mode(
            bridge.LIVE_MODE, env_file_directory=tmp_path
        )
        assert evaluator is None
        assert "TYPESAFE_API_KEY" in note
        assert "provisional" in note.lower()

    def test_offline_mode_never_needs_a_key(self, tmp_path):
        evaluator, note = bridge.evaluator_for_mode(
            bridge.OFFLINE_MODE, env_file_directory=tmp_path
        )
        assert evaluator is not None
        assert note is None

    def test_suggested_bounds_come_from_real_dates(self):
        low, high = bridge.suggest_period_bounds(make_state())
        assert (low, high) == (dt.date(2026, 10, 22), dt.date(2026, 10, 29))

    def test_suggested_bounds_tolerate_undated_projects(self):
        state = ProjectState(
            project_id="P",
            captured_at=CAPTURED,
            tasks=[Task(id="T", name="T", duration_days=1.0)],
        )
        assert bridge.suggest_period_bounds(state) == (None, None)


class TestErrorText:
    def test_com_not_available_is_structured_not_a_traceback(self):
        exc = MspNotAvailableError("could not connect to Microsoft Project via COM")
        text = bridge.friendly_msp_error(exc)
        assert text.startswith("WHAT:")
        assert "WHY IT MATTERS:" in text
        assert "NEXT:" in text
        assert "pywin32" in text

    @pytest.mark.parametrize(
        "exc",
        [MspNotAvailableError("no pywin32"), RuntimeError("mtime mismatch")],
    )
    def test_no_stack_trace_leaks(self, exc):
        assert "Traceback" not in bridge.friendly_msp_error(exc)


class TestTerminalDashboard:
    async def test_fresh_launch_is_honestly_offline_and_guided(self):
        app = DecisionTUI()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            connection = render(app.query_one("#connection"))
            assert "● OFFLINE" in connection
            assert "LIVE MSP" not in connection
            # the FIRST screen teaches the 4-step workflow: load first
            stepper = render(app.query_one("#queue_state"))
            assert "▸ [L] Load the active Microsoft Project plan" in stepper
            # every workflow step is advertised BEFORE its turn
            assert "[P] Set the planning period" in stepper
            assert "[C] Confirm the candidate session" in stepper
            assert "[A] Run the decision analysis" in stepper
            # every advertised action is a REAL binding
            bound = {b.key for b in DecisionTUI.BINDINGS}
            assert {
                "l", "p", "c", "a", "j", "r", "t", "s",
                "question_mark", "q",
            } <= bound

    async def test_apply_loaded_state_shows_real_context(self):
        app = DecisionTUI()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app._apply_loaded(make_state())
            await pilot.pause()
            assert "LIVE MSP" in render(app.query_one("#connection"))
            assert "CONNECTED" not in render(app.query_one("#connection"))
            name = render(app.query_one("#info_project"))
            assert "Test Facility Commissioning" in name
            note = render(app.query_one("#info_project_note"))
            assert "3 activities" in note
            assert "2 resources" in note
            assert "as of 10-21" in note  # compact snapshot date (fits the tile)
            tasks_metric = render(app.query_one("#m_activities"))
            assert "ACTIVITIES" in tasks_metric

    async def test_error_state_is_honest_and_never_connected(self):
        app = DecisionTUI()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app._apply_load_error(
                MspNotAvailableError("could not connect to Microsoft Project via COM")
            )
            await pilot.pause()
            connection = render(app.query_one("#connection"))
            assert "ERROR" in connection
            assert "LIVE MSP" not in connection
            message = render(app.query_one("#queue_state"))
            assert message.startswith("WHAT:")
            assert "pywin32" in message
            assert "ERROR" in render(app.query_one("#info_source"))

    async def test_period_confirm_analyze_flows_through_real_pipeline(self):
        app = DecisionTUI()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app._apply_loaded(make_state())
            await pilot.pause()

            # [P] [C] [A], applied through the same paths the keys trigger
            app._subject = set_planning_period(app._subject, PERIOD)
            app._subject = confirm_session(app._subject)
            result = run_decision(app._subject, bridge.offline_evaluator())
            app._apply_result(result)
            await pilot.pause()

            queue = app.query_one("#queue")
            assert len(queue.rows) == 3
            top = result.decision_result.ranked_tasks[0]
            assert top.task_name == "Roofing & cladding"
            assert top.resource_warning is True  # T1/T2 share Electrician

            review = result.decision_review
            assert review.status is ReviewStatus.REVIEW_REQUIRED

            title = render(app.query_one("#queue_title"))
            assert "REVIEW REQUIRED" in title
            assert "provisional" in title.lower()

            app._render_inspector(top.task_id)
            signals = render(app.query_one("#insp_signals"))
            assert "PROVISIONAL" in signals
            assert "not a live JEV result" in signals
            assert "Electrician" in signals
            legend = render(app.query_one("#bottom-note"))
            assert "resource conflict" in legend

    async def test_stepper_advances_with_the_workflow(self):
        app = DecisionTUI()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()

            def current_step() -> str:
                step_lines = [
                    line.strip()
                    for line in render(app.query_one("#queue_state")).split("\n")
                    if line.strip().startswith("▸")
                ]
                assert len(step_lines) == 1  # exactly ONE current step
                return step_lines[0]

            app._apply_loaded(make_state())
            await pilot.pause()
            assert "[P]" in current_step()

            # the real [P] callback path (the modal return value feeds in)
            app._on_period_result(
                (PERIOD.start_date, PERIOD.end_date)
            )
            await pilot.pause()
            assert "[C]" in current_step()

            app._subject = confirm_session(app._subject)
            app._refresh_queue_state()
            await pilot.pause()
            assert "[A]" in current_step()

    async def test_selection_updates_the_inspector(self):
        app = DecisionTUI()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app._apply_loaded(make_state())
            app._subject = set_planning_period(app._subject, PERIOD)
            app._subject = confirm_session(app._subject)
            result = run_decision(app._subject, bridge.offline_evaluator())
            app._apply_result(result)
            await pilot.pause()

            ranked_by_name = {
                item.task_name: item for item in result.decision_result.ranked_tasks
            }
            for name in ("Cable trays & containment", "Operator training"):
                app._render_inspector(ranked_by_name[name].task_id)
                assert name in render(app.query_one("#insp_title"))

    async def test_analysis_results_are_stale_guarded(self):
        app = DecisionTUI()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app._apply_loaded(make_state())
            app._subject = set_planning_period(app._subject, PERIOD)
            app._subject = confirm_session(app._subject)
            result = run_decision(app._subject, bridge.offline_evaluator())
            app._apply_result(result)
            await pilot.pause()
            assert len(app.query_one("#queue").rows) == 3
            # a DIFFERENT period creates a genuinely different session
            other_period = PlanningPeriod(dt.date(2026, 10, 22), dt.date(2026, 10, 28))
            app._subject = set_planning_period(app._subject, other_period)
            app._apply_result(result)
            await pilot.pause()
            # the result was computed for a DIFFERENT (old) session — the
            # app must refuse to display it AND remove its obsolete rows
            assert app._result is None
            assert len(app.query_one("#queue").rows) == 0

    async def test_period_modal_cancels_and_applies(self):
        app = DecisionTUI()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app._apply_loaded(make_state())
            await pilot.pause()

            await pilot.press("p")
            await pilot.pause()
            assert isinstance(app.screen, PeriodScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, PeriodScreen)
            assert app._subject.planning_period is None

            await pilot.press("p")
            await pilot.pause()
            screen = app.screen
            screen.query_one("#period_start", Input).value = "2026-10-22"
            end_input = screen.query_one("#period_end", Input)
            end_input.value = "2026-10-23"
            # the REAL Enter path: focus a field and press the key —
            # Input consumes Enter, so the modal must listen for its
            # Submitted event, not only a screen-level binding
            end_input.focus()
            await pilot.pause()
            import asyncio

            await pilot.press("enter")
            await pilot.pause()
            await asyncio.sleep(0.05)
            assert not isinstance(app.screen, PeriodScreen)
            assert app._subject.planning_period is not None
            assert app._subject.planning_period.start_date == dt.date(2026, 10, 22)
            # edits un-confirm the session — honest workflow semantics
            assert not app._subject.confirmed

    async def test_period_modal_rejects_bad_dates_inline(self):
        app = DecisionTUI()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app._apply_loaded(make_state())
            await pilot.press("p")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, PeriodScreen)
            screen.query_one("#period_start", Input).value = "not-a-date"
            screen.query_one("#period_end", Input).value = "2026-10-23"
            screen.action_apply()
            assert isinstance(app.screen, PeriodScreen)
            assert "ISO" in render(screen.query_one("#period_error"))

    async def test_help_screen_lists_the_real_keys_and_states(self):
        app = DecisionTUI()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("question_mark")
            await pilot.pause()
            from project_decision.tui.app import HelpScreen

            assert isinstance(app.screen, HelpScreen)
            help_text = " ".join(
                str(label.render()) for label in app.screen.query("Label")
            )
            assert "Show / hide the timeline" in help_text
            # the help lists EXACTLY what the footer advertises
            for line in HelpScreen.BINDING_LINES:
                assert line[0] in help_text
            assert "provisional" in help_text.lower()
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, HelpScreen)

    async def test_timeline_toggles_and_auto_hides_on_short_screens(self):
        # at 40 rows the timeline yields its space to the queue panels
        app = DecisionTUI()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            timeline = app.query_one("#timeline")
            assert timeline.display is False  # auto-hidden: nothing overlaps
            await pilot.press("t")
            await pilot.pause()
            assert timeline.display is True  # an explicit [T] wins
            await pilot.press("t")
            await pilot.pause()
            assert timeline.display is False

        tall = DecisionTUI()
        async with tall.run_test(size=(150, 46)) as pilot:
            await pilot.pause()
            timeline = tall.query_one("#timeline")
            assert timeline.display is True  # 46 rows fit the full design

    async def test_jev_toggle_stays_offline_without_a_key(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)  # no .env with a key anywhere near cwd
        app = DecisionTUI()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("j")
            await pilot.pause()
            assert "JEV OFFLINE" in render(app.query_one("#connection"))

    async def test_jev_toggle_goes_live_only_with_a_key(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TYPESAFE_API_KEY", "hermetic-test-key")
        monkeypatch.chdir(tmp_path)
        app = DecisionTUI()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("j")
            await pilot.pause()
            mode = render(app.query_one("#connection"))
            assert "JEV LIVE" in mode
            assert "JEV OFFLINE" not in mode
            # mode may only flip if a key genuinely resolved
            assert app._mode == bridge.LIVE_MODE

    async def test_live_evaluation_reaches_the_dashboard(self, monkeypatch):
        """A REAL evaluation object — LIVE labels are earned, never faked."""
        from project_decision.application.consequence_evaluation import (
            ConsequenceEvaluation,
        )

        evaluated = ConsequenceEvaluation(
            task_id="T1",
            status="evaluated",
            consequence_of_delay=2 / 3,
            jev_score=2,
            confidence=0.9,
            probabilities=None,
            legend=None,
            review_reasons=(),
            error_message=None,
            error_kind=None,
        )
        app = DecisionTUI()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app._apply_loaded(make_state())
            workflow = confirmed_workflow(app._subject.project_state)
            result = run_decision(workflow, lambda session, state: [evaluated])
            app._subject = workflow
            app._mode = bridge.LIVE_MODE
            app._apply_result(result)
            await pilot.pause()

            top = result.decision_result.ranked_tasks[0]
            assert top.consequence_source == "evaluated"
            app._render_inspector(top.task_id)
            signals = render(app.query_one("#insp_signals"))
            assert "LIVE JEV" in signals
            assert "confidence 0.90" in signals


class TestDisplayWidgets:
    async def test_score_bar_shows_no_data_for_missing_values(self):
        from project_decision.tui.widgets import ScoreBarRow

        app = DecisionTUI()
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            bar = app.query_one("#insp_sp", ScoreBarRow)
            await pilot.pause()
            app._subject = None
            bar.set_criterion("SP", None, None)
            assert "no data" in render(bar)

            bar.set_criterion("SI", 0.5, 10.0)
            assert "+10.0 pts" in render(bar)

    async def test_timeline_renders_only_real_dated_rows(self):
        from project_decision.tui.widgets import TimelineChart

        app = DecisionTUI()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            chart = app.query_one("#timeline", TimelineChart)
            app._apply_loaded(make_state())
            await pilot.pause()
            text = render(chart)
            assert "TIMELINE" in text
            # the undated task is NOT invented onto the chart
            state = make_state()
            state.tasks[0].start = None
            chart.render_schedule(
                labels=[t.name for t in state.tasks],
                starts=[t.start for t in state.tasks],
                finishes=[t.finish for t in state.tasks],
                window_start=dt.date(2026, 10, 22),
                window_end=dt.date(2026, 10, 29),
            )
            text = render(chart)
            assert "undated" in text
