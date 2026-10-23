"""PROJECT DECISION — the terminal dashboard, Version 2 design.

Visual system (from the approved Version 2 reference):

- near-black background (#10141b) with restrained teal (#55d6be) and
  cyan/blue (#8ab4ff) accents, warn (#ffbd69) / bad (#ff8294);
- a header strip, brand row with a real status chip, a three-box
  project strip (ACTIVE PROJECT / ANALYSIS WINDOW / DATA SOURCE);
- four KPI metric cards with label / value / note;
- a 2fr PRIORITY QUEUE panel next to a 1fr SELECTED ACTIVITY panel
  with styled signal lines, score-breakdown bars and a NEXT ACTION;
- a collapsible TIMELINE panel (press [T]);
- a context footer and a disclaimer line.

Usability system (Version 2):

- the FIRST screen shows a 4-step workflow (Load → Period → Confirm →
  Analyze) with the current step highlighted; every later state says
  what the next action is and why an action is unavailable;
- every visible shortcut matches its real binding (footer + [?]
  help screen lists the same bindings);
- evidence labels are honest: connection status is live only after a
  real read; offline/provisional consequences are never displayed as
  live JEV results; stale results are refused, never shown.

Everything displayed comes from the existing application layer through
``tui.data`` — project data, periods, sessions, results, reviews,
conflicts. NOTHING is hardcoded from a design reference, no demo mode
exists, and no change is ever written back to Microsoft Project
(read-only COM; connect → read → disconnect per load).

Keyboard (stable; footer and [?] help mirror it exactly):
    L load the active Microsoft Project plan (read-only, worker)
    P planning period — set / change the window
    C confirm the candidate session (explicit, like the data model)
    A run the decision analysis for the confirmed session
    J toggle JEV consequence mode (OFFLINE provisional / LIVE when a
      TYPESAFE_API_KEY is actually configured)
    R re-capture the project snapshot
    T toggle the timeline panel
    S sample-project guide (the bundled real .mpp workflow)
    ? context help (all keys and states, same list as here)
    ↑/↓ select activities (also Enter) — the queue drives the inspector
    Q quit
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Label, Static

from project_decision.application.decision_pipeline import (
    DecisionPipelineResult,
)
from project_decision.application.decision_review import ReviewStatus
from project_decision.application.decision_workflow import (
    DecisionWorkflowState,
    confirm_session,
    load_project,
    result_is_stale,
    run_decision,
    set_planning_period,
)
from project_decision.domain.project_state import ProjectState
from project_decision.domain import PlanningPeriod
from project_decision.infrastructure.msp.exceptions import MspError
from project_decision.tui import data as bridge
from project_decision.tui.widgets import (
    ACCENT,
    BLUE,
    DIM,
    ERROR,
    FAINT,
    GOOD,
    TEXT,
    WARN,
    ScoreBarRow,
    StatCard,
    TimelineChart,
)

WORKFLOW_STEPS = [
    ("[L]", "Load the active Microsoft Project plan"),
    ("[P]", "Set the planning period"),
    ("[C]", "Confirm the candidate session"),
    ("[A]", "Run the decision analysis"),
]


class PeriodScreen(ModalScreen[tuple[dt.date, dt.date] | None]):
    """Modal: configure the planning period (start/end, inclusive)."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "apply", "Apply period"),
    ]

    def __init__(self, start_iso: str | None, end_iso: str | None) -> None:
        super().__init__()
        self._start_iso = start_iso
        self._end_iso = end_iso

    def compose(self) -> ComposeResult:
        with Container(id="period_dialog"):
            yield Label("▸  PLANNING PERIOD", id="period_title")
            yield Label("Which activities need attention? The period is inclusive.", id="period_hint")
            yield Label("START DATE (YYYY-MM-DD)", classes="field-label")
            yield Input(
                value=self._start_iso or "",
                placeholder="e.g. 2026-10-22",
                id="period_start",
            )
            yield Label("END DATE (YYYY-MM-DD)", classes="field-label")
            yield Input(
                value=self._end_iso or "",
                placeholder="e.g. 2026-11-06",
                id="period_end",
            )
            yield Label("", id="period_error")
            yield Label(
                "[ENTER] apply · [ESC] cancel — the candidates come from "
                "the real schedule; nothing is guessed",
                markup=False,
                id="period_footer",
            )

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """ENTER inside a date field applies the period.

        Input widgets consume the Enter key themselves (they emit their
        own Submitted event), so the screen binding alone would never
        fire while a field is focused — this is the REAL Enter path.
        """
        self.action_apply()

    def action_apply(self) -> None:
        start_widget = self.query_one("#period_start", Input)
        end_widget = self.query_one("#period_end", Input)
        error_widget = self.query_one("#period_error", Label)
        try:
            start = dt.date.fromisoformat(start_widget.value.strip())
            end = dt.date.fromisoformat(end_widget.value.strip())
        except ValueError:
            error_widget.update(
                Text(" dates must be ISO YYYY-MM-DD ", style=f"bold {ERROR}")
            )
            return
        if start > end:
            error_widget.update(
                Text(" start must not be after end ", style=f"bold {ERROR}")
            )
            return
        self.dismiss((start, end))


class HelpScreen(ModalScreen[None]):
    """Modal: every key, every state — one honest page."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("question_mark", "close", "Close"),
        Binding("h", "close", "Close"),
    ]

    BINDING_LINES = [
        ("[L]", "Load the active Microsoft Project plan (read-only COM snapshot)"),
        ("[R]", "Re-capture the same plan later (read-only again)"),
        ("[P]", "Set / change the planning period (inclusive window)"),
        ("[C]", "Confirm the candidate session (explicit workflow step)"),
        ("[A]", "Run the decision analysis (offline provisional or LIVE JEV)"),
        ("[J]", "Switch JEV mode; refuses loudly when no API key is configured"),
        ("[T]", "Show / hide the timeline panel"),
        ("[S]", "Sample-project guide in the queue panel (real bundled .mpp)"),
        ("[?]", "This help"),
        ("↑/↓, Enter", "Select an activity in the priority queue"),
        ("[Q]", "Quit"),
    ]

    HONESTY_LINES = [
        "● CONNECTED appears only after a real read; ◉ during work; ● ERROR on failure.",
        "Offline consequences are provisional 0.5 and flagged — never shown as live JEV.",
        "LIVE JEV is one API call per candidate; failures become structured review flags.",
        "Results for a changed session are discarded (stale protection), never displayed.",
        "Read-only: nothing is written, saved, or closed in Microsoft Project.",
    ]

    def compose(self) -> ComposeResult:
        with Container(id="help_dialog"):
            yield Label("▸  HELP — keys & honest states", id="help_title")
            for key, description in self.BINDING_LINES:
                yield Label(
                    Text.assemble((f" {key:<12}", f"bold {ACCENT}"), (description, DIM)),
                    classes="help-line",
                )
            yield Label("HOW STATES ARE SHOWN", classes="help-heading")
            for honesty_line in self.HONESTY_LINES:
                yield Label(f" · {honesty_line}", classes="help-line")
            yield Label("[ESC] close", markup=False, id="help_footer")

    def action_close(self) -> None:
        self.dismiss(None)


class DecisionTUI(App[None]):
    """The PROJECT DECISION terminal dashboard (Version 2 design)."""

    CSS = """
    $navy: #10141b;
    $panel: #151b24;
    $card: #19212c;
    $border: #303b4a;
    $accent: #55d6be;
    $blue: #8ab4ff;
    $warn: #ffbd69;
    $invalid: #ff8294;
    $text: #e7edf5;
    $dim: #a8b6c9;
    $faint: #718198;

    Screen {
        background: $navy;
        color: $text;
    }
    Header {
        background: #171e28;
        color: $dim;
        height: 3;
    }
    Footer {
        background: #171e28;
        color: $dim;
    }
    #page {
        padding: 0 2;
        height: 1fr;
    }
    #brand-row {
        height: 1;
        margin-bottom: 1;
    }
    #brand {
        color: $accent;
        text-style: bold;
        width: 1fr;
    }
    #connection {
        color: $accent;
        text-style: bold;
        width: auto;
        content-align: right middle;
    }
    #project-strip {
        height: 5;
        margin-bottom: 1;
    }
    .info-box {
        background: $card;
        border: round $border;
        padding: 0;
        width: 1fr;
        height: 5;
        margin-right: 1;
    }
    .info-box:last-child { margin-right: 0; }
    .info-title {
        color: $faint;
        text-style: bold;
    }
    .info-value {
        color: $text;
        text-style: bold;
    }
    .info-note {
        color: $dim;
    }
    #metrics {
        height: 5;
        margin-bottom: 1;
    }
    StatCard {
        background: $card;
        border: round $border;
        padding: 0 1;
        width: 1fr;
        height: 5;
        margin-right: 1;
    }
    StatCard:last-child { margin-right: 0; }
    #timeline {
        height: 12;
        background: $panel;
        border: round $border;
        margin-bottom: 1;
    }
    #main-row {
        height: 1fr;
        min-height: 9;
    }
    #queue-panel {
        width: 2fr;
        border: round $border;
        background: $panel;
        padding: 0 1;
        margin-right: 1;
    }
    #side-panel {
        width: 1fr;
        min-width: 44;
        border: round $border;
        background: $panel;
        padding: 0 1;
    }
    .section-title {
        color: $accent;
        text-style: bold;
        height: 2;
        padding-top: 1;
    }
    DataTable {
        height: 1fr;
        background: $panel;
        border: none;
    }
    DataTable > .datatable--header {
        background: #202a37;
        color: $text;
        text-style: bold;
    }
    DataTable > .datatable--cursor {
        background: #24433f;
        color: #ffffff;
    }
    #queue_state {
        height: 1fr;
        color: $dim;
        padding: 1 1;
    }
    #side-heading {
        color: $text;
        text-style: bold;
    }
    .side-copy {
        color: $dim;
        height: auto;
    }
    .side-heading-label {
        color: $faint;
        text-style: bold;
    }
    ScoreBarRow { height: 1; }
    #insp_facts { height: auto; color: $dim; }
    #insp_notes { height: auto; color: $faint; }
    #bottom-note {
        height: 1;
        color: $faint;
    }
    PeriodScreen {
        align: center middle;
    }
    HelpScreen {
        align: center middle;
    }
    #period_dialog {
        width: 64;
        height: auto;
        background: $card;
        border: round $accent;
        padding: 1;
        margin: 1 2;
    }    #period_title { color: $accent; text-style: bold; }
    #period_hint, #period_footer { color: $dim; }
    .field-label { color: $faint; text-style: bold; margin-top: 1; }
    #period_error { color: $invalid; text-style: bold; }
    #help_dialog {
        width: 90;
        height: auto;
        max-height: 34;
        background: $card;
        border: round $accent;
        padding: 1;
        margin: 1 2;
    }
    #help_title { color: $accent; text-style: bold; margin-bottom: 1; }
    .help-line { color: $dim; }
    .help-heading {
        color: $faint;
        text-style: bold;
        padding-top: 1;
    }
    #help_footer { color: $faint; padding-top: 1; }
    #side-panel, #queue, #timeline {
        scrollbar-color: #34455a;
        scrollbar-background: #151b24;
    }
    """

    TITLE = "PROJECT DECISION"
    SUB_TITLE = "PROJECT INTELLIGENCE TERMINAL"

    BINDINGS = [
        Binding("l", "load", "Load plan", show=True, key_display="L"),
        Binding("p", "period", "Period", show=True, key_display="P"),
        Binding("c", "confirm", "Confirm", show=True, key_display="C"),
        Binding("a", "analyze", "Analyze", show=True, key_display="A"),
        Binding("j", "toggle_jev", "JEV mode", show=True, key_display="J"),
        Binding("r", "reload", "Re-capture", show=True, key_display="R"),
        Binding("s", "sample_guide", "Sample", show=True, key_display="S"),
        Binding("t", "toggle_timeline", "Timeline", show=True, key_display="T"),
        Binding(
            "question_mark", "help", "Help", show=True, key_display="?"
        ),
        Binding("q", "quit", "Quit", show=True, key_display="Q"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._subject: DecisionWorkflowState | None = None
        self._result: DecisionPipelineResult | None = None
        self._mode: str = bridge.OFFLINE_MODE
        self._connection_kind: str = "disconnected"
        self._queue_rows: list[str] = []  # row order → task_id
        self._ranked_by_id: dict[str, Any] = {}
        self._panel_message: str | None = None  # sample guide / sticky text
        self._timeline_user: bool | None = None  # explicit [T] override

    # -- composition -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="page"):
            with Horizontal(id="brand-row"):
                yield Label(
                    "◆  PROJECT DECISION  /  DECISION SUPPORT", id="brand"
                )
                yield Static("● OFFLINE", id="connection")
            with Horizontal(id="project-strip"):
                with Vertical(classes="info-box"):
                    yield Label("ACTIVE PROJECT", classes="info-title")
                    yield Label("no project loaded", classes="info-value", id="info_project")
                    yield Label(
                        "press [L] to load the active plan",
                        markup=False,
                        classes="info-note",
                        id="info_project_note",
                    )
                with Vertical(classes="info-box"):
                    yield Label("ANALYSIS WINDOW", classes="info-title")
                    yield Label("not set", classes="info-value", id="info_window")
                    yield Label(
                        "press [P]", markup=False, classes="info-note", id="info_window_note"
                    )
                with Vertical(classes="info-box"):
                    yield Label("DATA SOURCE", classes="info-title")
                    yield Label("● DISCONNECTED", classes="info-value", id="info_source")
                    yield Label(
                        "JEV OFFLINE (provisional)",
                        classes="info-note",
                        id="info_source_note",
                    )
            with Horizontal(id="metrics"):
                yield StatCard(id="m_activities")
                yield StatCard(id="m_candidates")
                yield StatCard(id="m_review")
                yield StatCard(id="m_flags")
            yield TimelineChart("", id="timeline")
            with Horizontal(id="main-row"):
                with Vertical(id="queue-panel"):
                    yield Label("▸  PRIORITY QUEUE", classes="section-title", id="queue_title")
                    yield Static("", id="queue_state", markup=False)
                    yield DataTable(
                        cursor_type="row",
                        show_cursor=True,
                        zebra_stripes=True,
                        id="queue",
                    )
                with VerticalScroll(id="side-panel"):
                    yield Label("▸  SELECTED ACTIVITY", classes="section-title")
                    yield Label("—", id="insp_title")
                    yield Label("", id="insp_score")
                    yield Label("SIGNALS", classes="side-heading-label")
                    yield Static("", id="insp_signals")
                    yield ScoreBarRow("", id="insp_sp")
                    yield ScoreBarRow("", id="insp_si")
                    yield ScoreBarRow("", id="insp_cd")
                    yield Static("", id="insp_facts")
                    yield Static("", id="insp_notes")
                    yield Label("NEXT ACTION", classes="side-heading-label")
                    yield Static("", id="insp_next")
            yield Label(
                "Read-only Microsoft Project integration — nothing is "
                "written back · press [?] for help",
                markup=False,
                id="bottom-note",
            )
        yield Footer()

    def on_mount(self) -> None:
        queue = self.query_one("#queue", DataTable)
        queue.add_columns("RANK", "ACTIVITY", "INDEX", "SP", "SI", "CD", "FLAGS")
        widths = (6, 30, 7, 6, 5, 5, 9)
        for column, width in zip(queue.columns.values(), widths):
            column.width = width
        # launch placeholders: every panel says what it needs next —
        # never an empty, unexplained frame (Version 2 usability)
        self._metric("m_activities", "ACTIVITIES", "—", "no project loaded")
        self._metric("m_candidates", "CANDIDATES", "—", "set a period [P]")
        self._metric("m_review", "NEEDS REVIEW", "—", "known after analysis")
        self._metric("m_flags", "RESOURCE FLAGS", "—", "conflicts load with [L]")
        self.query_one("#timeline", TimelineChart).update(
            Text(
                " TIMELINE — no project loaded yet; press [L] to load the "
                "active plan (or [S] for the sample guide)",
                style=DIM,
            )
        )
        self._show_stepper()
        self._set_connection("disconnected")
        self._layout_timeline(self.size.height)

    def on_resize(self, event) -> None:
        self._layout_timeline(event.size.height)
        self._fit_queue_columns()

    def _fit_queue_columns(self) -> None:
        """Keep ALL queue columns visible even on narrow terminals.

        The activity column absorbs the available width; every value
        column stays narrow and visible (with values truncating inside
        the activity column before any column disappears).
        """
        queue = self.query_one("#queue", DataTable)
        panel = self.query_one("#queue-panel")
        declared = panel.size.width if panel.size is not None else 0
        available = max(36, (declared or self.size.width * 2 // 3) - 6)
        fixed = 5 + 6 + 6 + 5 + 5 + 8  # rank, index, sp, si, cd, flags
        activity = min(38, max(14, available - fixed))
        widths = (5, activity, 6, 6, 5, 5, 8)
        for column, width in zip(list(queue.columns.values()), widths):
            column.width = width

    # -- timeline visibility ---------------------------------------------------

    def _layout_timeline(self, terminal_height: int) -> None:
        """Auto-hide the timeline on short terminals unless [T]-pinned.

        The full Version 2 composition (timeline visible) needs ~44
        rows; below that the timeline yields and the queue/inspector
        keep the space — the queue panels stay readable, nothing
        overlaps (the layout budget is fixed panels + 1fr main row).
        """
        timeline = self.query_one("#timeline", TimelineChart)
        if self._timeline_user is None:
            timeline.display = terminal_height >= 44
        else:
            timeline.display = self._timeline_user

    def action_toggle_timeline(self) -> None:
        timeline = self.query_one("#timeline", TimelineChart)
        self._timeline_user = not timeline.display
        timeline.display = self._timeline_user

    # -- selection -------------------------------------------------------------

    def on_data_table_cursor_row_changed(self, event) -> None:
        row_index = event.cursor_row
        if 0 <= row_index < len(self._queue_rows):
            self._render_inspector(self._queue_rows[row_index])

    def on_data_table_row_selected(self, event) -> None:
        self.on_data_table_cursor_row_changed(event)

    # -- state helpers -----------------------------------------------------------

    def _set_connection(self, kind: str, detail: str = "") -> None:
        """The status chip. Real states only, as in the reference design."""
        self._connection_kind = kind
        chip = self.query_one("#connection", Static)
        mode_tag = (
            "JEV OFFLINE" if self._mode == bridge.OFFLINE_MODE else "JEV LIVE"
        )
        if kind == "connected":
            chip.update(
                Text("● LIVE MSP", style=f"bold {GOOD}").append(
                    f" | {mode_tag}", style=f"bold {DIM}"
                ),
            )
        elif kind == "loading":
            chip.update(Text(f"◉ {detail or 'READING MSP…'}", style=f"bold {WARN}"))
        elif kind == "error":
            chip.update(Text("● ERROR", style=f"bold {ERROR}"))
        else:
            chip.update(
                Text("● OFFLINE", style=f"bold {DIM}").append(
                    f" | {mode_tag}", style=f"bold {FAINT}"
                ),
            )

    def _info(self, value_id: str, value: str, note: str) -> None:
        self.query_one(f"#{value_id}", Label).update(value)
        self.query_one(f"#{value_id}_note", Label).update(note)

    def _metric(self, metric_id: str, title: str, value: str, detail: str = "") -> None:
        self.query_one(f"#{metric_id}", StatCard).set_metric(title, value, detail)

    def _stepper(self) -> Text:
        """The 4-step workflow guide with the current step highlighted."""
        if self._subject is None:
            current = 0
        elif self._subject.planning_period is None:
            current = 1
        elif not self._subject.confirmed:
            current = 2
        elif self._result is None:
            current = 3
        else:
            current = 4
        text = Text()
        text.append(" WORKFLOW\n", style=f"bold {FAINT}")
        for index, (key, description) in enumerate(WORKFLOW_STEPS):
            if index < current:
                text.append(f"  ✓ ", style=GOOD)
                text.append(f"{key} {description}\n", style=FAINT)
            elif index == current:
                text.append(f"  ▸ ", style=f"bold {ACCENT}")
                text.append(f"{key} {description}\n", style=f"bold {ACCENT}")
            else:
                text.append(f"  · ", style=FAINT)
                text.append(f"{key} {description}\n", style=FAINT)
        if current == 4:
            text.append("  Workflow complete — pick activities in the queue.", style=DIM)
        else:
            text.append(
                "  Each step unlocks the next; reasons for unavailable",
                style=FAINT,
            )
            text.append("\n  actions appear when you press their key.\n", style=FAINT)
        return text

    def _refresh_queue_state(self) -> None:
        """Stepper (default) or a sticky message (guide / error)."""
        state_widget = self.query_one("#queue_state", Static)
        queue = self.query_one("#queue", DataTable)
        if self._panel_message is not None:
            queue.display = False
            state_widget.display = True
            state_widget.update(self._panel_message)
            return
        has_rows = len(self._queue_rows) > 0
        queue.display = has_rows
        state_widget.display = not has_rows
        state_widget.update(self._stepper())

    def _show_stepper(self) -> None:
        self._panel_message = None
        self._refresh_queue_state()

    def _ns_stale(self) -> bool:
        """Whether the current result belongs to an older workflow state."""
        return result_is_stale(self._subject, self._result)

    def _window(
        self, state: DecisionWorkflowState | None
    ) -> tuple[str, str]:
        """(value, note) for the ANALYSIS WINDOW info box."""
        if state is None:
            return "not set", "press [L] first"
        period = state.planning_period
        if period is None:
            return "not set", "press [P] to choose the window"
        value = (
            f"{period.start_date.isoformat()} → {period.end_date.isoformat()}"
        )
        if self._ns_stale():
            note = f"{period.working_days} working days · stale — press [A]"
        elif state.confirmed:
            note = (
                f"{period.working_days} working days · press [A]"
                if self._result is None
                else f"{period.working_days} working days · [A] to re-run"
            )
        else:
            note = f"{period.working_days} working days · confirm [C]"
        return value, note

    def _source(
        self, kind: str
    ) -> tuple[str, str]:
        """(value, note) for the DATA SOURCE info box — always honest."""
        mode_note = (
            "JEV OFFLINE (provisional)"
            if self._mode == bridge.OFFLINE_MODE
            else "JEV LIVE — 1 API call per candidate"
        )
        if kind == "connected":
            return "Microsoft Project · read-only", (
                "active doc · " + mode_note
            )
        if kind == "error":
            return "● COM ERROR", "see queue panel · " + mode_note
        return "● DISCONNECTED", mode_note

    def _bottom_note(self, text: Text | None) -> None:
        widget = self.query_one("#bottom-note", Label)
        widget.update(
            text
            if text is not None
            else Text(
                " Read-only Microsoft Project integration — nothing is "
                "written back · press [?] for help",
                style=FAINT,
            )
        )

    # -- [L] / [R]: load + re-capture -------------------------------------------

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
        self._panel_message = " Attaching to Microsoft Project via COM… (read-only)."
        self._refresh_queue_state()
        self._worker_load()

    def action_reload(self) -> None:
        self.action_load()

    def _apply_loaded(self, state: ProjectState) -> None:
        self._subject = load_project(lambda: state)
        self._result = None
        self._ranked_by_id = {}
        self._queue_rows = []
        self._set_connection("connected")
        completed = sum(1 for t in state.tasks if t.progress >= 1.0)
        in_progress = sum(1 for t in state.tasks if 0.0 < t.progress < 1.0)
        critical = sum(1 for t in state.tasks if t.is_critical is True)
        self._info(
            "info_project",
            state.project_name or state.project_id,
            f"{len(state.tasks)} activities · {len(state.resources)} resources"
            f" · as of {state.captured_at.strftime('%m-%d')}"
            + (
                f" · status date {state.status_date.isoformat()}"
                if state.status_date is not None
                and state.status_date != state.captured_at.date()
                else ""
            ),
        )
        self._info("info_window", *self._window(self._subject))
        value, note = self._source("connected")
        self._info("info_source", value, note)
        self._metric(
            "m_activities",
            "ACTIVITIES",
            str(len(state.tasks)),
            (
                f"{completed} done · {in_progress} started"
                if in_progress or completed
                else "none started"
            ),
        )
        self._metric("m_candidates", "CANDIDATES", "—", "set a period [P]")
        self._metric("m_review", "NEEDS REVIEW", "—", "known after analysis")
        self._metric(
            "m_flags",
            "RESOURCE FLAGS",
            "—",
            f"{critical} critical-path" if critical else "not reported yet",
        )
        if state.capture_warnings:
            self._bottom_note(
                Text(" ● capture warnings: ", style=f"bold {WARN}")
                .append(" ".join(state.capture_warnings)[:120], style=DIM)
            )
        else:
            self._bottom_note(None)
        self._render_schedule_draft(state)
        self._show_stepper()
        self._set_queue_title(None)
        self.notify(
            f"Loaded {len(state.tasks)} activities from "
            f"{state.project_id or 'Microsoft Project'} (read-only)."
        )

    def _apply_load_error(self, exc: MspError) -> None:
        self._subject = None
        self._result = None
        self._queue_rows = []
        self._set_connection("error")
        self.query_one("#info_project", Label).update("no project loaded")
        self.query_one("#info_project_note", Label).update(
            "the COM read failed — details in the queue panel"
        )
        self._info("info_window", *self._window(None))
        value, note = self._source("error")
        self._info("info_source", value, note)
        self._metric("m_activities", "ACTIVITIES", "—", "load failed")
        self._metric("m_candidates", "CANDIDATES", "—", "—")
        self._metric("m_review", "NEEDS REVIEW", "—", "—")
        self._metric("m_flags", "RESOURCE FLAGS", "—", "—")
        self._panel_message = bridge.friendly_msp_error(exc)
        self._refresh_queue_state()
        self._bottom_note(None)
        self.notify("Microsoft Project could not be read (see queue panel).", severity="error")

    # -- [S] sample guide ----------------------------------------------------------

    def action_sample_guide(self) -> None:
        self._panel_message = bridge.sample_project_guide_text()
        self._refresh_queue_state()
        self.notify("Sample-project guide shown in the queue panel.", markup=False)

    # -- [P] period -------------------------------------------------------------------

    def action_period(self) -> None:
        if self._subject is None:
            self.notify("Load a project first — press [L].", severity="warning", markup=False)
            return
        state = self._subject.project_state
        if self._subject.planning_period is not None:
            start = self._subject.planning_period.start_date.isoformat()
            end = self._subject.planning_period.end_date.isoformat()
        else:
            low, high = bridge.suggest_period_bounds(state)
            start = low.isoformat() if low is not None else ""
            end = high.isoformat() if high is not None else ""
        self.push_screen(PeriodScreen(start, end), self._on_period_result)

    def _on_period_result(self, result: tuple[dt.date, dt.date] | None) -> None:
        if result is None:
            return
        start, end = result
        self._subject = set_planning_period(
            self._subject, PlanningPeriod(start_date=start, end_date=end)
        )
        session = self._subject.planning_session
        self._metric(
            "m_candidates",
            "CANDIDATES",
            str(len(session.candidate_task_ids)),
            f"{self._subject.planning_period.working_days} working days",
        )
        self._info("info_window", *self._window(self._subject))
        self._set_queue_title(self._result)
        self._bottom_note(None)
        self._show_stepper()
        self.notify(
            f"Period set — {len(session.candidate_task_ids)} candidates "
            "discovered. Press [C] to confirm.", markup=False)

    # -- [C] confirm -----------------------------------------------------------------

    def action_confirm(self) -> None:
        if self._subject is None:
            self.notify("Load a project first — press [L].", severity="warning", markup=False)
            return
        if self._subject.planning_period is None:
            self.notify("Set a planning period first — press [P].", severity="warning", markup=False)
            return
        try:
            self._subject = confirm_session(self._subject)
        except ValueError as exc:
            self.notify(str(exc), severity="error", markup=False)
            return
        self.notify("Session confirmed — press [A] to run the decision analysis.", markup=False)
        self._show_stepper()

    # -- [?] help --------------------------------------------------------------------

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    # -- [J] JEV mode --------------------------------------------------------------------

    def action_toggle_jev(self) -> None:
        if self._mode == bridge.OFFLINE_MODE:
            evaluator, note = bridge.evaluator_for_mode(bridge.LIVE_MODE)
            if evaluator is None:
                self.notify(note or "LIVE JEV unavailable; staying offline.", severity="warning")
                return
            self._mode = bridge.LIVE_MODE
            self.notify(
                "JEV LIVE — the next analysis evaluates consequences through "
                "the configured TypeSafe integration (1 API call per candidate)."
            )
        else:
            self._mode = bridge.OFFLINE_MODE
            self.notify(
                "JEV OFFLINE — consequences will be provisional (0.5) and "
                "flagged for review."
            )
        self._refresh_status_after_mode_change()

    def _refresh_status_after_mode_change(self) -> None:
        """Re-derive the chip + source box from the last connection state."""
        self._set_connection(self._connection_kind)
        value, note = self._source(
            "connected"
            if self._connection_kind == "connected"
            else ("error" if self._connection_kind == "error" else "disconnected")
        )
        self._info("info_source", value, note)

    # -- [A] analyze ----------------------------------------------------------------------

    def action_analyze(self) -> None:
        if self._subject is None:
            self.notify("Load a project first — press [L].", severity="warning", markup=False)
            return
        if self._subject.planning_session is None:
            self.notify("Set a planning period first — press [P].", severity="warning", markup=False)
            return
        if not self._subject.confirmed:
            self.notify(
                "The session is not confirmed — press [C] (the data model "
                "requires a deliberate confirmation).",
                severity="warning", markup=False)
            return
        evaluator, note = bridge.evaluator_for_mode(self._mode)
        if note:
            self.notify(note, severity="warning")
            return
        mode = self._mode
        session_size = len(self._subject.planning_session.candidate_task_ids)
        if mode == bridge.LIVE_MODE:
            self._set_connection(
                "loading",
                f"ANALYZING — live JEV: 1 API call per candidate ({session_size})…",
            )
            self._panel_message = (
                f" ◉ Analyzing — LIVE JEV, one API call per candidate "
                f"({session_size} calls). This can take a while."
            )
        else:
            self._set_connection("loading", "ANALYZING…")
            self._panel_message = (
                " ◉ Analyzing — offline consequences (provisional 0.5, flagged)."
            )
        self._refresh_queue_state()
        self._worker_analyze(evaluator)

    @work(exclusive=True, thread=True, group="analyze")
    def _worker_analyze(self, evaluator) -> None:
        try:
            result = run_decision(self._subject, evaluator)
        except Exception as exc:  # the run itself failed — report honestly
            self.call_from_thread(self._apply_run_error, exc)
            return
        self.call_from_thread(self._apply_result, result)

    def _apply_run_error(self, exc: Exception) -> None:
        self._set_connection("error")
        self._panel_message = (
            f" Analysis failed.\n\n {exc.__class__.__name__}: {str(exc)[:160]}\n\n"
            " Nothing was changed in Microsoft Project; the loaded state is "
            "unchanged. Press [A] to try again."
        )
        self._refresh_queue_state()
        self.notify("The decision analysis failed — see the queue panel.", severity="error")

    def _apply_result(self, result: DecisionPipelineResult) -> None:
        if self._ns_stale_for(result):
            # The workflow moved on mid-run (reload/new period): never
            # display a result computed for a different session.
            self.notify(
                "Analysis discarded — the workflow changed while it ran.",
                severity="warning",
            )
            # discard BOTH the result and the now-misleading queue rows
            self._result = None
            self._ranked_by_id = {}
            self._queue_rows = []
            queue = self.query_one("#queue", DataTable)
            queue.clear(columns=False)
            self._show_stepper()
            self._set_queue_title(None)
            self._set_connection(
                "connected" if self._subject is not None else "disconnected"
            )
            if self._subject is not None:
                self._info("info_window", *self._window(self._subject))
            return
        self._result = result
        state = self._subject.project_state
        self._set_connection("connected")
        ranked = result.decision_result.ranked_tasks
        self._ranked_by_id = {item.task_id: item for item in ranked}

        queue = self.query_one("#queue", DataTable)
        queue.clear(columns=False)
        self._queue_rows = [item.task_id for item in ranked]
        for item in ranked:
            queue.add_row(
                Text(str(item.rank), style=f"bold {ACCENT}"),
                Text(str(item.task_name[:38]), style=TEXT),
                self._fmt(item.total_score, "{:.1f}"),
                self._fmt(item.schedule_pressure, "{:.3f}"),
                self._fmt(item.successor_impact, "{:.3f}"),
                self._fmt(item.consequence_of_delay, "{:.3f}"),
                Text(self._flags_of(item), style=self._flag_color(item)),
                key=item.task_id,
            )
        self._panel_message = None
        self._refresh_queue_state()

        review = result.decision_review
        needing_review = len(review.tasks_needing_review)
        self._metric(
            "m_candidates",
            "CANDIDATES",
            str(review.candidate_count),
            self._period_label(result),
        )
        self._metric(
            "m_review",
            "NEEDS REVIEW",
            str(needing_review),
            "reasons in queue + flags",
        )
        conflicts = len(result.conflict_analysis.conflicts)
        self._metric(
            "m_flags",
            "RESOURCE FLAGS",
            str(conflicts),
            "shared-resource clashes",
        )
        self._set_queue_title(result)
        self._bottom_note(
            Text(" flags: ", style=FAINT)
            .append("C", style=f"bold {DIM}")
            .append(" critical · ", style=FAINT)
            .append("R", style=f"bold {BLUE}")
            .append(" resource conflict · ", style=FAINT)
            .append("V", style=f"bold {WARN}")
            .append(" review · ", style=FAINT)
            .append("D", style=f"bold {FAINT}")
            .append(" no data", style=FAINT)
        )
        self._render_schedule_from_result(result)
        if ranked:
            queue.focus()
            self._render_inspector(ranked[0].task_id)
        self.notify(
            f"Analysis complete — {len(ranked)} activities ranked "
            f"({'LIVE JEV' if self._mode == bridge.LIVE_MODE else 'offline provisional'})."
        )

    def _ns_stale_for(self, result: DecisionPipelineResult) -> bool:
        return result_is_stale(self._subject, result)

    # -- helpers for rendering results -----------------------------------------

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
        return {evaluation.task_id: evaluation for evaluation in evaluations}

    @staticmethod
    def _flags_of(ranked) -> str:
        parts: list[str] = []
        if ranked.is_critical is True:
            parts.append("C")
        if ranked.resource_warning:
            parts.append("R")
        if ranked.needs_review:
            parts.append("V")
        if ranked.total_slack_days is None:
            parts.append("D")
        return " ".join(parts)

    @staticmethod
    def _flag_color(ranked) -> str:
        if ranked.resource_warning:
            return BLUE
        if ranked.needs_review:
            return WARN
        return DIM

    def _period_label(self, result: DecisionPipelineResult) -> str:
        period = result.decision_result.planning_period
        return f"{period.start_date.isoformat()} → {period.end_date.isoformat()}"

    def _set_queue_title(self, result: DecisionPipelineResult | None) -> None:
        title = self.query_one("#queue_title", Label)
        if result is None:
            title.update("▸  PRIORITY QUEUE")
            return
        review = result.decision_review
        label = {
            ReviewStatus.READY: "READY",
            ReviewStatus.REVIEW_REQUIRED: "REVIEW REQUIRED",
            ReviewStatus.INVALID: "INVALID",
        }.get(review.status, review.status.value.upper())
        pieces = []
        for key, short in (
            ("provisional_consequence", "provisional"),
            ("resource_conflict", "conflict"),
            ("missing_schedule_data", "no-data"),
            ("low_consequence_confidence", "low-conf"),
            ("consequence_evaluation_failed", "jev-failed"),
            ("consequence_evaluation_unavailable", "jev-n/a"),
            ("unscored_candidate", "unscored"),
        ):
            count = review.review_reason_counts.get(key)
            if count:
                pieces.append(f"{short} {count}")
        suffix = ("  ·  " + " · ".join(pieces)) if pieces else ""
        prefix = "STALE — press [A] to re-run  ·  " if self._ns_stale() else ""
        status_style = (
            f"bold {GOOD}"
            if review.status is ReviewStatus.READY
            else f"bold {WARN}"
        )
        header = Text.assemble(
            (f"{prefix}▸  PRIORITY QUEUE — ", f"bold {ACCENT}"),
            (f"● {label}", status_style),
        )
        if suffix:
            header.append(suffix, style=DIM)
        title.update(header)

    # -- timeline ----------------------------------------------------------------

    def _render_schedule_draft(self, state: ProjectState) -> None:
        start, finish = bridge.suggest_period_bounds(state)
        if start is None or finish is None:
            self.query_one("#timeline", TimelineChart).update(
                Text(" TIMELINE — no dated activities reported yet", style=DIM)
            )
            return
        dated = [t for t in state.tasks if t.start is not None and t.finish is not None]
        dated.sort(key=lambda t: (t.start, t.finish, t.id))
        subset = dated[:8]
        chart = self.query_one("#timeline", TimelineChart)
        chart.render_schedule(
            labels=[task.name for task in subset],
            starts=[task.start for task in subset],
            finishes=[task.finish for task in subset],
            window_start=start,
            window_end=finish,
            completed=[task.progress >= 1.0 for task in subset],
            progress=[task.progress for task in subset],
            today=state.status_date or state.captured_at.date(),
            title="TIMELINE — draft schedule (candidates fill this after analysis)",
        )

    def _render_schedule_from_result(self, result: DecisionPipelineResult) -> None:
        state = self._subject.project_state
        period = result.decision_result.planning_period
        ranked = result.decision_result.ranked_tasks
        tasks_by_id = {task.id: task for task in state.tasks}
        rows = [item for item in ranked if item.task_id in tasks_by_id]
        subset = rows[:8]
        chart = self.query_one("#timeline", TimelineChart)
        labels, starts, finishes, completed, progress = [], [], [], [], []
        for item in subset:
            task = tasks_by_id[item.task_id]
            labels.append(f"#{item.rank} {task.name}")
            starts.append(task.start)
            finishes.append(task.finish)
            completed.append(task.progress >= 1.0)
            progress.append(task.progress)
        chart.render_schedule(
            labels=labels,
            starts=starts,
            finishes=finishes,
            window_start=period.start_date,
            window_end=period.end_date,
            completed=completed,
            progress=progress,
            today=state.status_date or state.captured_at.date(),
            title="TIMELINE — candidates (rank order)",
        )

    # -- inspector -----------------------------------------------------------------

    def _pressure_word(self, value: float | None) -> tuple[str, str | None]:
        """A *descriptive* word for the REAL schedule-pressure value."""
        if value is None:
            return "unknown — no slack data was reported", WARN
        if value >= 2 / 3:
            return f"high (SP {value:.3f})", ERROR
        if value >= 1 / 3:
            return f"moderate (SP {value:.3f})", WARN
        return f"low (SP {value:.3f})", DIM

    def _render_inspector(self, task_id: str) -> None:
        ranked = self._ranked_by_id.get(task_id)
        if ranked is None or self._subject is None:
            return
        state = self._subject.project_state
        tasks_by_id = {task.id: task for task in state.tasks}
        task = tasks_by_id.get(task_id)
        evaluation = self._evaluation_index(self._result).get(task_id)

        title = self.query_one("#insp_title", Label)
        title.update(f"{ranked.task_name}")

        score_line = self.query_one("#insp_score", Label)
        score_line_id = "insp_score"
        unscored = ranked.score is None

        signals = Text()
        proportion_word, proportion_color = self._pressure_word(ranked.schedule_pressure)
        signals.append(" ▲ ", style=f"bold {proportion_color}")
        signals.append(f"Schedule pressure is {proportion_word}\n", style=proportion_color)

        if ranked.successor_count is None:
            signals.append(" ↳ ", style=f"bold {WARN}")
            signals.append("Successor impact unknown — flagged for review\n", style=WARN)
        elif ranked.successor_count > 0:
            signals.append(" ↳ ", style=f"bold {WARN}")
            signals.append(
                f"{ranked.successor_count} downstream activities depend on this\n",
                style=WARN,
            )
        else:
            signals.append(" ↳ ", style=f"bold {DIM}")
            signals.append("No downstream dependencies\n", style=DIM)

        if ranked.consequence_source == "evaluated":
            confidence = (
                f" (confidence {evaluation.confidence:.2f})"
                if evaluation is not None and evaluation.confidence is not None
                else ""
            )
            signals.append(" ◇ ", style=f"bold {GOOD}")
            signals.append(
                f"Consequence evaluated by LIVE JEV{confidence}\n", style=GOOD
            )
        else:
            signals.append(" ◇ ", style=f"bold {WARN}")
            signals.append(
                "Consequence PROVISIONAL 0.500 — not a live JEV result\n",
                style=WARN,
            )
        if (
            not unscored
            and ranked.score_range is not None
            and ranked.score_range.minimum != ranked.score_range.maximum
        ):
            signals.append(
                f"     · index range {ranked.score_range.minimum:.1f}–"
                f"{ranked.score_range.maximum:.1f}\n",
                style=FAINT,
            )

        if ranked.resource_warning:
            signals.append(" ⚑ ", style=f"bold {BLUE}")
            signals.append(
                "Shared resource: "
                + ", ".join(ranked.shared_resource_names)
                + " (overlapping work)\n",
                style=BLUE,
            )
        elif self._result is not None:
            signals.append(" ⚑ ", style=f"bold {DIM}")
            signals.append("No resource conflict in this session\n", style=DIM)
        else:
            signals.append(" ⚑ ", style=f"bold {FAINT}")
            signals.append("No analysis yet\n", style=FAINT)

        if ranked.total_slack_days is None:
            signals.append(" ▲ ", style=f"bold {WARN}")
            signals.append("Missing schedule data — flagged for review\n", style=WARN)

        self.query_one("#insp_signals", Static).update(signals)

        # score breakdown bars
        if unscored:
            for widget_id in ("insp_sp", "insp_si", "insp_cd"):
                self.query_one(f"#{widget_id}", ScoreBarRow).set_criterion("—", None, None)
        else:
            sp, si, cd = ranked.criterion_contributions
            self.query_one("#insp_sp", ScoreBarRow).set_criterion(
                "SP", ranked.schedule_pressure, sp
            )
            self.query_one("#insp_si", ScoreBarRow).set_criterion(
                "SI", ranked.successor_impact, si
            )
            self.query_one("#insp_cd", ScoreBarRow).set_criterion(
                "CD", ranked.consequence_of_delay, cd
            )

        # score headline
        if unscored:
            score_line.update(
                Text(" unscored — missing schedule data", style=f"bold {WARN}")
            )
        else:
            headline = Text()
            headline.append("Priority index  ", style=DIM)
            headline.append(f"{ranked.total_score:.1f} / 100", style=f"bold {ACCENT}")
            score_line.update(headline)

        # facts (+ notes)
        facts = Text()
        if task is not None:
            progress_pct = f"{task.progress * 100:.0f}%"
            facts.append(" STATUS ", style=FAINT)
            facts.append(
                "completed"
                if task.progress >= 1.0
                else (f"in progress ({progress_pct})" if task.progress > 0 else "not started"),
                style=DIM,
            )
            facts.append("\n DATES ", style=FAINT)
            if task.start is not None and task.finish is not None:
                facts.append(
                    f"{task.start.isoformat()} → {task.finish.isoformat()}"
                    f" ({task.duration_days:.1f}d)",
                    style=DIM,
                )
            else:
                facts.append("—", style=DIM)
            facts.append("\n SLACK ", style=FAINT)
            facts.append(
                f"{ranked.total_slack_days:.1f} wd"
                if ranked.total_slack_days is not None
                else "unknown — review",
                style=DIM,
            )
            facts.append("\n CRITICAL ", style=FAINT)
            facts.append(
                {True: "yes", False: "no"}.get(ranked.is_critical, "not reported"),
                style=DIM,
            )
            facts.append("\n SUCCESSORS ", style=FAINT)
            facts.append(
                str(ranked.successor_count)
                if ranked.successor_count is not None
                else "unknown",
                style=DIM,
            )
            facts.append("\n RANK ", style=FAINT)
            facts.append(f"#{ranked.rank} of {len(self._queue_rows)}", style=DIM)
        else:
            facts.append(" The activity row is not in the loaded snapshot.", style=WARN)
        self.query_one("#insp_facts", Static).update(facts)

        notes = Text()
        task_notes = (task.notes if task is not None else None) or None
        notes.append(" NOTES   ", style=FAINT)
        if task_notes:
            label = task_notes.strip()
            notes.append(
                (label[:170] + "…") if len(label) > 170 else label, style=FAINT
            )
        else:
            notes.append(
                "none — LIVE JEV evaluates consequences from name + notes", style=FAINT
            )
        self.query_one("#insp_notes", Static).update(notes)

        # contextual next action (never invented data — only guidance)
        next_action = Text()
        next_action.append(" ", style=DIM)
        if unscored:
            next_action.append(
                "Restore the missing schedule data in Microsoft Project and "
                "press [R] to re-capture; the activity stays unscored until then.",
                style=DIM,
            )
        else:
            actions = []
            if ranked.resource_warning:
                actions.append(
                    "coordinate the shared resource "
                    + ", ".join(ranked.shared_resource_names)
                    + " before committing this window"
                )
            if ranked.consequence_source != "evaluated":
                if self._mode == bridge.OFFLINE_MODE:
                    actions.append(
                        "press [J] then [A] to evaluate the consequence live, "
                        "or validate the provisional 0.5 with the team"
                    )
                else:
                    actions.append(
                        "this consequence could not be evaluated (e.g. no "
                        "notes) — validate the provisional value with the team"
                    )
            if ranked.review_reasons:
                actions.append("clear the review flags before acting on it")
            if not actions:
                actions.append(
                    "use this ranking position in the period decision; no "
                    "review items are attached"
                )
            next_action.append(
                "; ".join(actions[:2]) + ".", style=DIM
            )
        self.query_one("#insp_next", Static).update(next_action)
