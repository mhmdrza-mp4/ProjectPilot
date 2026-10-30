"""ProjectPilot — the terminal dashboard (cockpit design)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Label, Static
from textual.widgets.data_table import ColumnKey

from project_decision.application.decision_pipeline import DecisionPipelineResult
from project_decision.application.decision_review import ReviewStatus
from project_decision.application.decision_workflow import (
    DecisionWorkflowState,
    confirm_session,
    load_project,
    result_is_stale,
    run_decision,
    set_planning_period,
)
from project_decision.domain import PlanningPeriod
from project_decision.domain.project_state import ProjectState
from project_decision.infrastructure.msp.exceptions import MspError
from project_decision.tui import data as bridge
from project_decision.tui.theme import (
    COCKPIT,
    DEFAULT_THEME,
    PALETTES,
    css_variables,
    pal,
    textual_theme,
)
from project_decision.tui.widgets import (
    Brief,
    Hero,
    Inspector,
    KeyBar,
    Metric,
    Note,
    QueueHeader,
    Timeline,
    TopBar,
    parse_report,
)

HINTS = (
    "Open a plan in Microsoft Project, then press L to read it (read-only).",
    "Plan captured. Press P to choose the planning period.",
    "Candidates found. Press C to confirm the session.",
    "Session confirmed. Press A to run the analysis.",
    "Analysis ready — ↑ ↓ to inspect activities · A to re-run · R to re-capture.",
)


# -- modals --------------------------------------------------------------------------


class PeriodScreen(ModalScreen[tuple[dt.date, dt.date] | None]):
    """Choose the planning period (start/end, inclusive)."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "apply", "Apply period"),
    ]

    def __init__(self, start_iso: str | None, end_iso: str | None) -> None:
        super().__init__()
        self._start_iso = start_iso
        self._end_iso = end_iso

    def compose(self) -> ComposeResult:
        with Container(classes="dialog", id="period_dialog"):
            yield Label("PLANNING PERIOD", classes="dlg-title")
            yield Static("The window the candidate activities are discovered in (inclusive).", classes="dlg-sub")
            yield Label("START  ·  YYYY-MM-DD", classes="field-label")
            yield Input(value=self._start_iso or "", placeholder="e.g. 2026-10-22", id="period_start")
            yield Label("END  ·  YYYY-MM-DD", classes="field-label")
            yield Input(value=self._end_iso or "", placeholder="e.g. 2026-11-06", id="period_end")
            yield Label("", id="period_info")
            yield Label("ENTER apply   ·   ESC cancel", id="period_footer", markup=False)

    def on_mount(self) -> None:
        self._preview()
        self.query_one("#period_start", Input).focus()

    def _parse(self) -> tuple[dt.date, dt.date] | str:
        try:
            start = dt.date.fromisoformat(self.query_one("#period_start", Input).value.strip())
            end = dt.date.fromisoformat(self.query_one("#period_end", Input).value.strip())
        except ValueError:
            return "dates must be ISO YYYY-MM-DD"
        if start > end:
            return "start must not be after end"
        return start, end

    def _preview(self, *, strict: bool = False) -> None:
        P = pal(self)
        info = self.query_one("#period_info", Label)
        parsed = self._parse()
        if isinstance(parsed, str):
            blank = not (self.query_one("#period_start", Input).value.strip()
                         or self.query_one("#period_end", Input).value.strip())
            info.update(Text(f"✖ {parsed}", style=f"bold {P.bad}") if strict or not blank else "")
        else:
            span = (parsed[1] - parsed[0]).days + 1
            info.update(Text(f"✓ {span} calendar days", style=P.good))

    def on_input_changed(self, event: Input.Changed) -> None:
        self._preview()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "period_start":
            self.query_one("#period_end", Input).focus()
            return
        self.action_apply()

    def action_apply(self) -> None:
        parsed = self._parse()
        if isinstance(parsed, str):
            self._preview(strict=True)
            return
        self.dismiss(parsed)


class HelpScreen(ModalScreen[None]):
    """Every key, every state, one honest page."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("question_mark", "close", "Close"),
        Binding("h", "close", "Close"),
    ]

    KEYS = (
        ("L", "Load the active Microsoft Project plan (read-only COM snapshot)"),
        ("R", "Re-capture the same plan later (read-only again)"),
        ("P", "Set or change the planning period (inclusive window)"),
        ("C", "Confirm the candidate session (explicit workflow step)"),
        ("A", "Run the decision analysis (offline provisional or LIVE JEV)"),
        ("J", "Switch JEV mode; refuses loudly when no API key is configured"),
        ("T", "Show or hide the timeline"),
        ("S", "Guide to the bundled sample project"),
        ("↑ ↓", "Move through the priority queue; the inspector follows"),
        ("Ctrl+T", "Cycle colour theme (cockpit · glacier · daylight)"),
        ("Q", "Quit"),
    )
    HONESTY = (
        "● MSP LIVE appears only after a real read; the spinner shows work in progress; ✖ COM ERROR on failure.",
        "Offline consequences are provisional 0.5 and flagged, never shown as live JEV.",
        "LIVE JEV is one API call per candidate; failures become structured review flags.",
        "Results for a changed session are discarded (stale protection), never displayed.",
        "Read-only: nothing is written, saved, or closed in Microsoft Project.",
    )

    def compose(self) -> ComposeResult:
        P = pal(self)
        with VerticalScroll(classes="dialog", id="help_dialog"):
            yield Label("KEYBOARD & GUARANTEES", classes="dlg-title")
            for key, desc in self.KEYS:
                yield Static(Text.assemble((f" {key} ".center(8), f"bold {P.accent} on {P.raised}"),
                                           ("  " + desc, P.ink)), classes="help-line")
            yield Label("QUEUE FLAGS", classes="dlg-head")
            yield Static(Text.assemble(
                ("C", f"bold {P.bad}"), (" critical path   ", P.dim),
                ("R", f"bold {P.info}"), (" resource conflict   ", P.dim),
                ("V", f"bold {P.warn}"), (" needs review   ", P.dim),
                ("D", f"bold {P.faint}"), (" missing data", P.dim)), classes="help-line")
            yield Label("HOW STATES ARE SHOWN", classes="dlg-head")
            for line in self.HONESTY:
                yield Static(Text("· " + line, style=P.dim), classes="help-line")
            yield Label("ESC close", id="help_footer", markup=False)

    def action_close(self) -> None:
        self.dismiss(None)


class SampleScreen(ModalScreen[None]):
    """The bundled sample project guide (text comes from the bridge)."""

    BINDINGS = [Binding("escape", "close", "Close"), Binding("s", "close", "Close"),
                Binding("q", "close", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog", id="sample_dialog"):
            yield Label("SAMPLE PROJECT", classes="dlg-title")
            yield Static(bridge.sample_project_guide_text(), markup=False, id="sample_text")
            yield Label("ESC close", id="help_footer", markup=False)

    def action_close(self) -> None:
        self.dismiss(None)


# -- the app -----------------------------------------------------------------------------


class DecisionTUI(App[None]):
    """The ProjectPilot terminal dashboard."""

    CSS = """
    Screen { background: $pp-bg; color: $pp-ink; }
    * { scrollbar-size-vertical: 1; scrollbar-color: $pp-line; scrollbar-color-hover: $pp-faint;
        scrollbar-background: $pp-bg; scrollbar-corner-color: $pp-bg; }

    #body { height: 1fr; padding: 0 2 1 2; }
    #kpis { height: 4; margin-top: 1; }
    Metric { width: 1fr; background: $pp-surface; padding: 0 1; margin-right: 1; }
    Metric:last-of-type { margin-right: 0; }
    #main { height: 1fr; margin-top: 1; }
    #left { width: 1fr; padding-right: 2; }
    #right { width: 46; min-width: 46; background: $pp-panel; padding: 1 2; }
    #main.-narrow #right { width: 40; min-width: 40; }
    #main.-empty #right { display: none; }
    #main.-empty #left { padding-right: 0; }

    #queue { height: 1fr; background: $pp-bg; border: none; padding: 0; overflow-x: hidden; }
    #queue > .datatable--header { background: $pp-bg; color: $pp-faint; text-style: bold; }
    #queue > .datatable--cursor { background: $pp-raised; color: $pp-ink; text-style: bold; }
    #queue > .datatable--hover { background: $pp-surface; }
    #queue > .datatable--fixed { background: $pp-bg; }

    Timeline { margin-top: 1; border-top: solid $pp-line; padding-top: 1; }

    ModalScreen { align: center middle; background: $pp-bg 75%; }
    .dialog { background: $pp-panel; border: round $pp-line; padding: 1 3; height: auto; max-height: 90%; }
    #period_dialog { width: 62; }
    #help_dialog { width: 96; }
    #sample_dialog { width: 100; }
    .dlg-title { color: $pp-accent; text-style: bold; }
    .dlg-sub { color: $pp-faint; margin-bottom: 1; }
    .dlg-head { color: $pp-faint; text-style: bold; margin-top: 1; }
    .field-label { color: $pp-faint; text-style: bold; margin-top: 1; }
    .help-line { color: $pp-dim; }
    Input { background: $pp-surface; border: round $pp-line; color: $pp-ink; width: 100%; }
    Input:focus { border: round $pp-accent; }
    #period_info { margin-top: 1; height: 1; }
    #period_footer, #help_footer { color: $pp-faint; margin-top: 1; }
    #sample_text { color: $pp-dim; margin-top: 1; }
    Toast { background: $pp-raised; color: $pp-ink; }
    KeyBar { link-style: none; link-style-hover: bold; }
    """

    TITLE = "ProjectPilot"
    SUB_TITLE = "decision support for Microsoft Project"

    BINDINGS = [
        Binding("l", "load", "Load", key_display="L"),
        Binding("p", "period", "Period", key_display="P"),
        Binding("c", "confirm", "Confirm", key_display="C"),
        Binding("a", "analyze", "Analyze", key_display="A"),
        Binding("j", "toggle_jev", "JEV", key_display="J"),
        Binding("r", "reload", "Re-capture", key_display="R"),
        Binding("t", "toggle_timeline", "Timeline", key_display="T"),
        Binding("s", "sample", "Sample", key_display="S"),
        Binding("ctrl+t", "cycle_theme", "Theme", show=False),
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("q", "quit", "Quit", key_display="Q"),
    ]

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables.update(css_variables(PALETTES.get(self.theme, COCKPIT)))
        return variables

    def __init__(self) -> None:
        super().__init__()
        for palette in PALETTES.values():
            self.register_theme(textual_theme(palette))
        self.theme = DEFAULT_THEME
        for name in [n for n in self.available_themes if n not in PALETTES]:
            self.unregister_theme(name)  # the palette picker only offers our themes
        self._subject: DecisionWorkflowState | None = None
        self._result: DecisionPipelineResult | None = None
        self._mode: str = bridge.OFFLINE_MODE
        self._connection_kind = "disconnected"
        self._queue_rows: list[str] = []
        self._ranked_by_id: dict[str, Any] = {}
        self._timeline_user: bool | None = None
        self._frame = 0
        self._narrow = False

    # -- composition -----------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")
        with Vertical(id="body"):
            yield Brief(id="brief")
            with Horizontal(id="kpis"):
                yield Metric(id="m_activities")
                yield Metric(id="m_candidates")
                yield Metric(id="m_review")
                yield Metric(id="m_flags")
            with Horizontal(id="main", classes="-empty"):
                with Vertical(id="left"):
                    yield QueueHeader(id="qhead")
                    yield Hero(id="hero")
                    yield DataTable(cursor_type="row", zebra_stripes=False, cell_padding=1, id="queue")
                    yield Note(id="note")
                    yield Timeline(id="timeline")
                with VerticalScroll(id="right"):
                    yield Inspector(id="inspector")
        yield KeyBar(id="keybar")

    def on_mount(self) -> None:
        self._narrow = self.size.width < 120
        self.query_one("#main").set_class(self._narrow, "-narrow")
        self._build_columns()
        self.query_one("#queue", DataTable).display = False
        self.query_one("#qhead").display = False
        self._reset_metrics()
        self.query_one("#timeline", Timeline).set_empty("press L to capture the active schedule")
        self.theme_changed_signal.subscribe(self, self._on_theme_changed)
        self.set_interval(0.1, self._tick)
        self._set_connection("disconnected")
        self._refresh_brief()
        self._sync_flow()
        self._layout_timeline(self.size.height)

    def _build_columns(self) -> None:
        """Wide terminals show SP/SI/CD in the queue; narrow ones leave them to the inspector."""
        queue = self.query_one("#queue", DataTable)
        queue.clear(columns=True)
        def head(label: str) -> Text:
            return Text(label, justify="right")  # headers sit exactly over right-aligned numbers

        queue.add_column(head("#"), key="rank", width=4)
        queue.add_column("ACTIVITY", key="activity", width=30)
        queue.add_column(head("INDEX"), key="index", width=7)
        if not self._narrow:
            queue.add_column(head("SP"), key="sp", width=5)
            queue.add_column(head("SI"), key="si", width=5)
            queue.add_column(head("CD"), key="cd", width=5)
        queue.add_column("FLAGS", key="flags", width=7)

    def _on_theme_changed(self, _theme) -> None:
        self.call_after_refresh(self._repaint)

    def _repaint(self) -> None:
        for widget in self.query("TopBar, Brief, Metric, QueueHeader, Note, Hero, Inspector, Timeline, KeyBar"):
            widget.refresh(layout=True)
        if self._result is not None:
            self._fill_queue(self._result.decision_result.ranked_tasks)

    def action_cycle_theme(self) -> None:
        names = list(PALETTES)
        self.theme = names[(names.index(self.theme) + 1) % len(names)] if self.theme in names else names[0]
        self.notify(f"Theme: {self.theme}", markup=False)

    def _tick(self) -> None:
        if self._connection_kind != "loading":
            return
        self._frame += 1
        for widget_id in ("#topbar", "#hero"):
            widget = self.query_one(widget_id)
            widget.frame = self._frame  # type: ignore[attr-defined]
            widget.refresh()

    def on_resize(self, event) -> None:
        narrow = event.size.width < 120
        if narrow != self._narrow:
            self._narrow = narrow
            self.query_one("#main").set_class(narrow, "-narrow")
            self._build_columns()
            if self._result is not None:
                self._fill_queue(self._result.decision_result.ranked_tasks)
        self._layout_timeline(event.size.height)
        self._fit_queue_columns()

    def _right_width(self) -> int:
        """Inspector width: 30% of the terminal on wide screens (46-66), fixed on narrow ones."""
        if self._narrow:
            return 40
        return max(46, min(66, round(self.size.width * 0.30)))

    def _fit_queue_columns(self) -> None:
        queue = self.query_one("#queue", DataTable)
        right_panel = self.query_one("#right")
        right_w = self._right_width()
        right_panel.styles.width = right_w
        right_panel.styles.min_width = right_w
        right = 0 if self.query_one("#main").has_class("-empty") else right_w + 2
        width = self.size.width - 4 - right  # body padding, inspector, gutter
        fixed = (4 + 7 + 7 + 3 * 2 if self._narrow else 4 + 7 + 5 + 5 + 5 + 7 + 7 * 2) + 2
        column = queue.columns.get(ColumnKey("activity"))
        if column is not None:
            column.width = max(14, width - fixed)  # activity takes whatever the numbers leave
            queue.refresh(layout=True)

    # -- timeline visibility -------------------------------------------------------------

    def _layout_timeline(self, terminal_height: int) -> None:
        timeline = self.query_one("#timeline", Timeline)
        if self._subject is None:
            timeline.display = False
            return
        timeline.display = (terminal_height >= 44) if self._timeline_user is None else self._timeline_user

    def action_toggle_timeline(self) -> None:
        if self._subject is None:
            self.notify("Load a project first — press L.", severity="warning", markup=False)
            return
        timeline = self.query_one("#timeline", Timeline)
        self._timeline_user = not timeline.display
        timeline.display = self._timeline_user

    # -- selection ----------------------------------------------------------------------

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is not None and event.row_key.value is not None:
            self._render_inspector(str(event.row_key.value))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key is not None and event.row_key.value is not None:
            self._render_inspector(str(event.row_key.value))

    # -- workflow state --------------------------------------------------------------------

    def _step(self) -> int:
        if self._subject is None:
            return 0
        if self._subject.planning_period is None:
            return 1
        if not self._subject.confirmed:
            return 2
        return 3 if self._result is None else 4

    def _ns_stale(self) -> bool:
        return result_is_stale(self._subject, self._result)

    def _sync_flow(self, hint: str | None = None) -> None:
        step = self._step()
        stale = self._result is not None and self._ns_stale()
        loaded = self._subject is not None
        top = self.query_one("#topbar", TopBar)
        top.step, top.stale = step, stale
        top.jev_live = self._mode == bridge.LIVE_MODE
        top.refresh()
        hero = self.query_one("#hero", Hero)
        hero.step = step
        hero.refresh()
        keys = self.query_one("#keybar", KeyBar)
        keys.step, keys.loaded = step, loaded
        self._sync_welcome()
        keys.hint = hint or ("Results are stale — press A to re-run the analysis." if stale else HINTS[step])
        keys.refresh()

    def _set_connection(self, kind: str, detail: str = "") -> None:
        self._connection_kind = kind
        top = self.query_one("#topbar", TopBar)
        top.conn, top.conn_detail = kind, detail
        top.jev_live = self._mode == bridge.LIVE_MODE
        top.refresh()

    def _metric(self, metric_id: str, title: str, value: str, detail: str = "", role: str = "accent") -> None:
        self.query_one(f"#{metric_id}", Metric).set_metric(title, value, detail, role)

    def _reset_metrics(self) -> None:
        self._metric("m_activities", "ACTIVITIES", "—", "no project loaded")
        self._metric("m_candidates", "CANDIDATES", "—", "set a period with P")
        self._metric("m_review", "NEEDS REVIEW", "—", "known after analysis")
        self._metric("m_flags", "RESOURCE FLAGS", "—", "loaded with the plan")

    def _window(self) -> tuple[str, str, str]:
        state = self._subject
        if state is None:
            return "", "", "faint"
        period = state.planning_period
        if period is None:
            return "window not set", "press P to choose it", "warn"
        value = f"{period.start_date.isoformat()} → {period.end_date.isoformat()}"
        wd = f"{period.working_days} working days"
        if self._ns_stale():
            return value, f"{wd} · stale — press A", "warn"
        if state.confirmed:
            return value, (f"{wd} · confirmed — press A" if self._result is None else f"{wd} · A to re-run"), "good"
        return value, f"{wd} · confirm with C", "dim"

    def _refresh_brief(self) -> None:
        brief = self.query_one("#brief", Brief)
        if self._subject is None:
            brief.set("No project loaded",
                      "the COM read failed — details below" if self._connection_kind == "error"
                      else "Waiting for a Microsoft Project plan")
            return
        state = self._subject.project_state
        stamp = (f"status {state.status_date.strftime('%m-%d')}"
                 if state.status_date is not None and state.status_date != state.captured_at.date()
                 else f"as of {state.captured_at.strftime('%m-%d')}")
        engine = "JEV live" if self._mode == bridge.LIVE_MODE else "JEV offline · provisional"
        meta = (f"{len(state.tasks)} tasks · {len(state.resources)} resources · {stamp}"
                f" · Microsoft Project, read-only · {engine}")
        value, note, role = self._window()
        brief.set(state.project_name or state.project_id, meta, value, note, role)

    def _show_hero(self, mode: str, title: str = "", detail: str = "",
                   blocks: list[tuple[str, str]] | None = None) -> None:
        hero = self.query_one("#hero", Hero)
        hero.mode, hero.title, hero.detail, hero.blocks = mode, title, detail, blocks or []
        self._refresh_main()
        hero.refresh()
        self._sync_welcome()

    def _welcome_visible(self) -> bool:
        """True while the hero banner (which already carries the name) is on screen."""
        hero = self.query_one("#hero", Hero)
        return hero.mode == "welcome" and hero.display

    def _sync_welcome(self) -> None:
        """On the welcome screen the banner, step list and read-only note live in
        the hero, so the chrome around it drops its duplicates."""
        welcome = self._welcome_visible()
        top = self.query_one("#topbar", TopBar)
        top.brand = top.rail = not welcome
        top.refresh()
        self.query_one("#note", Note).display = not welcome
        keys = self.query_one("#keybar", KeyBar)
        keys.welcome = welcome
        keys.refresh()

    def _refresh_main(self) -> None:
        has_rows = bool(self._queue_rows)
        hero = self.query_one("#hero", Hero)
        busy_or_error = hero.mode in ("busy", "error")
        show_table = has_rows and not busy_or_error
        self.query_one("#queue", DataTable).display = show_table
        self.query_one("#qhead").display = has_rows
        hero.display = not show_table
        self.query_one("#main").set_class(not has_rows, "-empty")
        self._fit_queue_columns()

    def _set_note(self, kind: str, detail: str = "") -> None:
        note = self.query_one("#note", Note)
        note.kind, note.detail = kind, detail
        note.refresh()

    # -- L / R: load + re-capture --------------------------------------------------------------

    @work(exclusive=True, thread=True, group="msp")
    def _worker_load(self) -> None:
        try:
            state = bridge.load_active_state()
        except MspError as exc:
            self.call_from_thread(self._apply_load_error, exc)
            return
        self.call_from_thread(self._apply_loaded, state)

    def action_load(self) -> None:
        self._set_connection("loading", "READING MSP…")
        self._show_hero("busy", "Attaching to Microsoft Project", "Reading the active plan through COM (read-only).")
        self._worker_load()

    def action_reload(self) -> None:
        self.action_load()

    def _apply_loaded(self, state: ProjectState) -> None:
        self._subject = load_project(lambda: state)
        self._result = None
        self._ranked_by_id = {}
        self._queue_rows = []
        self.query_one("#queue", DataTable).clear()
        self.query_one("#inspector", Inspector).clear()
        self._set_connection("connected")
        completed = sum(1 for t in state.tasks if t.progress >= 1.0)
        started = sum(1 for t in state.tasks if 0.0 < t.progress < 1.0)
        critical = sum(1 for t in state.tasks if t.is_critical is True)
        self._metric("m_activities", "ACTIVITIES", str(len(state.tasks)),
                     f"{completed} done · {started} started" if started or completed else "none started")
        self._metric("m_candidates", "CANDIDATES", "—", "set a period with P", "info")
        self._metric("m_review", "NEEDS REVIEW", "—", "known after analysis")
        self._metric("m_flags", "RESOURCE FLAGS", "—",
                     f"{critical} critical-path" if critical else "not reported yet")
        if state.capture_warnings:
            self._set_note("warn", " ".join(state.capture_warnings)[:160])
        else:
            self._set_note("readonly")
        self.query_one("#qhead", QueueHeader).status = None
        self._render_schedule_draft(state)
        self._show_hero("ready")
        self._layout_timeline(self.size.height)
        self._refresh_brief()
        self._sync_flow()
        self.notify(f"Loaded {len(state.tasks)} activities from {state.project_id or 'Microsoft Project'} (read-only).")

    def _apply_load_error(self, exc: MspError) -> None:
        self._subject = None
        self._result = None
        self._queue_rows = []
        self.query_one("#inspector", Inspector).clear()
        self._set_connection("error")
        self._reset_metrics()
        self._metric("m_activities", "ACTIVITIES", "—", "load failed", "bad")
        self._show_hero("error", "COULD NOT READ MICROSOFT PROJECT", blocks=parse_report(bridge.friendly_msp_error(exc)))
        self._layout_timeline(self.size.height)
        self._refresh_brief()
        self._sync_flow("Fix the cause above, then press L to try again.")
        self.notify("Microsoft Project could not be read (details on screen).", severity="error")

    # -- P period ---------------------------------------------------------------------------------

    def action_period(self) -> None:
        if self._subject is None:
            self.notify("Load a project first — press L.", severity="warning", markup=False)
            return
        if self._subject.planning_period is not None:
            start = self._subject.planning_period.start_date.isoformat()
            end = self._subject.planning_period.end_date.isoformat()
        else:
            low, high = bridge.suggest_period_bounds(self._subject.project_state)
            start = low.isoformat() if low is not None else ""
            end = high.isoformat() if high is not None else ""
        self.push_screen(PeriodScreen(start, end), self._on_period_result)

    def _on_period_result(self, result: tuple[dt.date, dt.date] | None) -> None:
        if result is None:
            return
        start, end = result
        self._subject = set_planning_period(self._subject, PlanningPeriod(start_date=start, end_date=end))
        session = self._subject.planning_session
        self._metric("m_candidates", "CANDIDATES", str(len(session.candidate_task_ids)),
                     f"{self._subject.planning_period.working_days} working days", "info")
        self._set_queue_header(self._result)
        self._refresh_brief()
        self._sync_flow()
        self.notify(f"Period set — {len(session.candidate_task_ids)} candidates discovered. Press C to confirm.",
                    markup=False)

    # -- C confirm ----------------------------------------------------------------------------------

    def action_confirm(self) -> None:
        if self._subject is None:
            self.notify("Load a project first — press L.", severity="warning", markup=False)
            return
        if self._subject.planning_period is None:
            self.notify("Set a planning period first — press P.", severity="warning", markup=False)
            return
        try:
            self._subject = confirm_session(self._subject)
        except ValueError as exc:
            self.notify(str(exc), severity="error", markup=False)
            return
        self._refresh_brief()
        self._sync_flow()
        self.notify("Session confirmed — press A to run the decision analysis.", markup=False)

    # -- ? help / S sample ---------------------------------------------------------------------------

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_sample(self) -> None:
        self.push_screen(SampleScreen())

    # -- J JEV mode -------------------------------------------------------------------------------------

    def action_toggle_jev(self) -> None:
        if self._mode == bridge.OFFLINE_MODE:
            evaluator, note = bridge.evaluator_for_mode(bridge.LIVE_MODE)
            if evaluator is None:
                self.notify(note or "LIVE JEV unavailable; staying offline.", severity="warning")
                return
            self._mode = bridge.LIVE_MODE
            self.notify("JEV LIVE — the next analysis evaluates consequences through the configured "
                        "TypeSafe integration (1 API call per candidate).")
        else:
            self._mode = bridge.OFFLINE_MODE
            self.notify("JEV OFFLINE — consequences will be provisional (0.5) and flagged for review.")
        self._set_connection(self._connection_kind, self.query_one("#topbar", TopBar).conn_detail)
        self._refresh_brief()
        self._sync_flow()

    # -- A analyze ------------------------------------------------------------------------------------------

    def action_analyze(self) -> None:
        if self._subject is None:
            self.notify("Load a project first — press L.", severity="warning", markup=False)
            return
        if self._subject.planning_session is None:
            self.notify("Set a planning period first — press P.", severity="warning", markup=False)
            return
        if not self._subject.confirmed:
            self.notify("The session is not confirmed — press C (the data model requires a deliberate confirmation).",
                        severity="warning", markup=False)
            return
        evaluator, note = bridge.evaluator_for_mode(self._mode)
        if note:
            self.notify(note, severity="warning")
            return
        size = len(self._subject.planning_session.candidate_task_ids)
        if self._mode == bridge.LIVE_MODE:
            self._set_connection("loading", f"ANALYZING · {size} JEV calls…")
            self._show_hero("busy", "Analyzing with LIVE JEV",
                            f"One API call per candidate ({size} calls). This can take a while.")
        else:
            self._set_connection("loading", "ANALYZING…")
            self._show_hero("busy", "Analyzing", "Offline consequences: provisional 0.5, flagged for review.")
        self._worker_analyze(evaluator)

    @work(exclusive=True, thread=True, group="analyze")
    def _worker_analyze(self, evaluator) -> None:
        try:
            result = run_decision(self._subject, evaluator)
        except Exception as exc:  # noqa: BLE001 - surfaced as a structured state
            self.call_from_thread(self._apply_run_error, exc)
            return
        self.call_from_thread(self._apply_result, result)

    def _apply_run_error(self, exc: Exception) -> None:
        self._set_connection("error")
        self._show_hero("error", "ANALYSIS FAILED", blocks=[
            ("WHAT", f"{exc.__class__.__name__}: {str(exc)[:200]}"),
            ("WHY", "The decision analysis could not complete, so no ranking is shown."),
            ("NEXT", "Nothing was changed in Microsoft Project and the loaded state is unchanged. Press A to try again."),
        ])
        self._sync_flow("The analysis failed — press A to try again.")
        self.notify("The decision analysis failed (details on screen).", severity="error")

    def _apply_result(self, result: DecisionPipelineResult) -> None:
        if result_is_stale(self._subject, result):
            self.notify("Analysis discarded — the workflow changed while it ran.", severity="warning")
            self._result = None
            self._ranked_by_id = {}
            self._queue_rows = []
            self.query_one("#queue", DataTable).clear()
            self._set_connection("connected" if self._subject is not None else "disconnected")
            self._show_hero("ready" if self._subject is not None else "welcome")
            self._set_queue_header(None)
            self._refresh_brief()
            self._sync_flow()
            return

        self._result = result
        self._set_connection("connected")
        ranked = result.decision_result.ranked_tasks
        self._ranked_by_id = {item.task_id: item for item in ranked}
        self._queue_rows = [item.task_id for item in ranked]
        self._fill_queue(ranked)

        review = result.decision_review
        needing = len(review.tasks_needing_review)
        self._metric("m_candidates", "CANDIDATES", str(review.candidate_count), self._period_label(result), "info")
        self._metric("m_review", "NEEDS REVIEW", str(needing), "reasons in the queue header",
                     "warn" if needing > 0 else "good")
        conflicts = len(result.conflict_analysis.conflicts)
        self._metric("m_flags", "RESOURCE FLAGS", str(conflicts), "shared-resource clashes",
                     "info" if conflicts > 0 else "good")
        self._set_queue_header(result)
        self._set_note("legend")
        self._render_schedule_from_result(result)
        self._show_hero("ready")
        self._refresh_brief()
        self._sync_flow()
        if ranked:
            queue = self.query_one("#queue", DataTable)
            queue.focus()
            queue.move_cursor(row=0)
            self._render_inspector(ranked[0].task_id)
        self.notify(f"Analysis complete — {len(ranked)} activities ranked "
                    f"({'LIVE JEV' if self._mode == bridge.LIVE_MODE else 'offline provisional'}).")

    # -- result rendering ---------------------------------------------------------------------------------------

    @staticmethod
    def _fmt(value, pattern) -> str:
        return pattern.format(value) if value is not None else "—"

    @staticmethod
    def _evaluation_index(result: DecisionPipelineResult) -> dict[str, Any]:
        evaluations = result.consequence_evaluations
        if evaluations is None:
            return {}
        if isinstance(evaluations, dict):
            return dict(evaluations)
        return {e.task_id: e for e in evaluations}

    def _period_label(self, result: DecisionPipelineResult) -> str:
        period = result.decision_result.planning_period
        return f"{period.start_date.isoformat()} → {period.end_date.isoformat()}"

    def _flags(self, P, ranked) -> Text:
        out = Text()
        tokens = []
        if ranked.is_critical is True:
            tokens.append(("C", P.bad))
        if ranked.resource_warning:
            tokens.append(("R", P.info))
        if ranked.needs_review:
            tokens.append(("V", P.warn))
        if ranked.total_slack_days is None:
            tokens.append(("D", P.faint))
        for i, (letter, color) in enumerate(tokens):
            out.append((" " if i else "") + letter, style=f"bold {color}")
        return out

    def _fill_queue(self, ranked) -> None:
        P = pal(self)
        queue = self.query_one("#queue", DataTable)
        cursor = queue.cursor_row
        queue.clear()
        for item in ranked:
            top = item.rank <= 3
            score = item.total_score
            index = Text(
                self._fmt(score, "{:.1f}"), justify="right",
                style=f"bold {P.accent}" if top else (P.ink if score is not None else P.warn),
            )

            def crit(value, hot):
                style = hot if value is not None and value >= 0.67 else P.dim
                return Text(self._fmt(value, "{:.2f}"), style=style, justify="right")

            cells = [
                Text(f"{item.rank}", style=f"bold {P.accent if top else P.faint}", justify="right"),
                Text(str(item.task_name)[:60], style=f"bold {P.ink}" if top else P.ink, no_wrap=True),
                index,
            ]
            if not self._narrow:
                cells += [
                    crit(item.schedule_pressure, P.bad),
                    crit(item.successor_impact, P.info),
                    crit(item.consequence_of_delay, P.warn if item.consequence_of_delay == 0.5 else P.violet),
                ]
            cells.append(self._flags(P, item))
            queue.add_row(*cells, key=item.task_id)
        self._queue_rows = [item.task_id for item in ranked]
        self._refresh_main()
        if ranked:
            queue.move_cursor(row=min(max(cursor, 0), len(ranked) - 1))

    def _set_queue_header(self, result: DecisionPipelineResult | None) -> None:
        head = self.query_one("#qhead", QueueHeader)
        if result is None:
            head.status, head.pieces, head.stale, head.count = None, [], False, None
            head.refresh()
            return
        review = result.decision_review
        label, role = {
            ReviewStatus.READY: ("READY", "good"),
            ReviewStatus.REVIEW_REQUIRED: ("REVIEW REQUIRED", "warn"),
            ReviewStatus.INVALID: ("INVALID", "bad"),
        }.get(review.status, (review.status.value.upper(), "warn"))
        pieces = []
        for key, short in (
            ("provisional_consequence", "provisional"), ("resource_conflict", "conflict"),
            ("missing_schedule_data", "no-data"), ("low_consequence_confidence", "low-conf"),
            ("consequence_evaluation_failed", "jev-failed"),
            ("consequence_evaluation_unavailable", "jev-n/a"), ("unscored_candidate", "unscored"),
        ):
            count = review.review_reason_counts.get(key)
            if count:
                pieces.append(f"{count} {short}")
        head.status, head.pieces = (label, role), pieces
        head.stale = self._ns_stale()
        head.count = len(result.decision_result.ranked_tasks)
        head.refresh()

    # -- timeline -------------------------------------------------------------------------------------------------------

    def _render_schedule_draft(self, state: ProjectState) -> None:
        timeline = self.query_one("#timeline", Timeline)
        start, finish = bridge.suggest_period_bounds(state)
        if start is None or finish is None:
            timeline.set_empty("no dated activities reported yet")
            return
        dated = sorted((t for t in state.tasks if t.start is not None and t.finish is not None),
                       key=lambda t: (t.start, t.finish, t.id))[:8]
        timeline.render_schedule(
            labels=[t.name for t in dated], starts=[t.start for t in dated], finishes=[t.finish for t in dated],
            window_start=start, window_end=finish, completed=[t.progress >= 1.0 for t in dated],
            progress=[t.progress for t in dated], today=state.status_date or state.captured_at.date(),
            title="TIMELINE — draft schedule",
        )

    def _render_schedule_from_result(self, result: DecisionPipelineResult) -> None:
        state = self._subject.project_state
        period = result.decision_result.planning_period
        tasks = {t.id: t for t in state.tasks}
        subset = [r for r in result.decision_result.ranked_tasks if r.task_id in tasks][:8]
        self.query_one("#timeline", Timeline).render_schedule(
            labels=[f"#{r.rank:<2} {tasks[r.task_id].name}" for r in subset],
            starts=[tasks[r.task_id].start for r in subset], finishes=[tasks[r.task_id].finish for r in subset],
            window_start=period.start_date, window_end=period.end_date,
            completed=[tasks[r.task_id].progress >= 1.0 for r in subset],
            progress=[tasks[r.task_id].progress for r in subset],
            today=state.status_date or state.captured_at.date(), title="TIMELINE — top candidates",
        )

    # -- inspector ----------------------------------------------------------------------------------------------------------

    def _render_inspector(self, task_id: str) -> None:
        ranked = self._ranked_by_id.get(task_id)
        if ranked is None or self._subject is None:
            return
        task = {t.id: t for t in self._subject.project_state.tasks}.get(task_id)
        evaluation = self._evaluation_index(self._result).get(task_id) if self._result is not None else None
        inspector = self.query_one("#inspector", Inspector)
        inspector.show(ranked, task, evaluation, self._result is not None, self._mode == bridge.OFFLINE_MODE)
        self.query_one("#right").scroll_home(animate=False)
