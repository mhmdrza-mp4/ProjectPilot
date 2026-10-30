"""Display widgets of the ProjectPilot terminal dashboard.

Pure display composition. Every widget stores plain data and draws it in
``render()`` from the active palette, so a theme switch or a terminal
resize repaints correctly without the app re-pushing anything.

They read EXISTING immutable result objects (``RankedTask``, domain
tasks) and contain no scoring, ranking, review, or conflict rules. A
missing value renders as an explicit "no data" bar/label, never as zero.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from rich.align import Align
from rich.console import Group
from rich.padding import Padding
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual.widget import Widget

from project_decision.tui.theme import Palette, pal

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_PARTIAL = " ▏▎▍▌▋▊▉█"

STEPS = (
    ("Load", "L", "Read the active Microsoft Project plan"),
    ("Period", "P", "Choose the planning window"),
    ("Confirm", "C", "Review and confirm the candidate session"),
    ("Analyze", "A", "Rank the candidates with explainable scores"),
)

_REASON_LABELS = {
    "provisional_consequence": "Consequence is provisional (0.5), not a live JEV result",
    "resource_conflict": "Shares a resource with another candidate",
    "missing_schedule_data": "Schedule data is missing",
    "low_consequence_confidence": "Low-confidence consequence evaluation",
    "consequence_evaluation_failed": "Live consequence evaluation failed",
    "consequence_evaluation_unavailable": "Consequence evaluation unavailable",
    "unscored_candidate": "Candidate could not be scored",
}


# -- drawing helpers ----------------------------------------------------------


def hbar(value: float | None, width: int, fill: str, track: str, faint: str) -> Text:
    """Smooth horizontal bar (eighth-block precision on a solid track)."""
    if value is None:
        return Text("╌" * width, style=faint)
    eighths = round(max(0.0, min(1.0, value)) * width * 8)
    full, part = divmod(eighths, 8)
    bar = Text()
    bar.append("█" * full, style=f"{fill} on {track}")
    used = full
    if part and used < width:
        bar.append(_PARTIAL[part], style=f"{fill} on {track}")
        used += 1
    bar.append(" " * (width - used), style=f"on {track}")
    return bar


def heading(P: Palette, label: str, right: str = "") -> Text:
    t = Text()
    t.append(label, style=f"bold {P.faint}")
    if right:
        t.append("  " + right, style=P.faint)
    return t


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def parse_report(text: str) -> list[tuple[str, str]]:
    """Split a WHAT / WHY IT MATTERS / NEXT message into labelled blocks."""
    blocks: list[tuple[str, str]] = []
    for chunk in text.split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        for tag, label in (("WHAT:", "WHAT"), ("WHY IT MATTERS:", "WHY"), ("NEXT:", "NEXT")):
            if chunk.startswith(tag):
                blocks.append((label, chunk[len(tag):].strip()))
                break
        else:
            blocks.append(("", chunk))
    return blocks


# -- top bar -------------------------------------------------------------------


class TopBar(Widget):
    """Brand, connection chips and the four-step workflow rail."""

    DEFAULT_CSS = "TopBar { height: 4; padding: 0 2; }"

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.step = 0
        self.conn = "disconnected"
        self.conn_detail = ""
        self.jev_live = False
        self.stale = False
        self.frame = 0
        self.brand = True  # hidden while the welcome banner already shows the name
        self.rail = True  # likewise: the welcome screen lists the same four steps

    def _chips(self, P: Palette) -> Text:
        t = Text()
        if self.stale:
            t.append("⚠ STALE", style=f"bold {P.warn}")
            t.append("   ")
        if self.conn == "connected":
            t.append("● MSP LIVE", style=f"bold {P.good}")
        elif self.conn == "loading":
            t.append(
                f"{SPINNER[self.frame % len(SPINNER)]} {self.conn_detail or 'WORKING…'}",
                style=f"bold {P.warn}",
            )
        elif self.conn == "error":
            t.append("✖ COM ERROR", style=f"bold {P.bad}")
        else:
            t.append("○ MSP OFFLINE", style=P.faint)
        t.append("   ")
        if self.jev_live:
            t.append("◆ JEV LIVE", style=f"bold {P.violet}")
        else:
            t.append("◇ JEV OFFLINE", style=P.dim)
        return t

    def render(self) -> Group:
        P = pal(self)
        w = max(40, self.size.width)
        left = Text()
        if self.brand:
            left.append("✦ ", style=f"bold {P.accent}")
            left.append("Project", style=f"bold {P.ink}")
            left.append("Pilot", style=f"bold {P.accent}")
        right = self._chips(P)
        gap = max(2, w - left.cell_len - right.cell_len)
        line1 = Text.assemble(left, " " * gap, right)

        rail = Text()
        for i, (name, _key, _d) in enumerate(STEPS if self.rail else ()):
            if i:
                rail.append("  ───  ", style=P.good if i <= self.step else P.line)
            if i < self.step:
                rail.append(f"✓ {name}", style=P.good)
            elif i == self.step:
                rail.append(f"● {name}", style=f"bold {P.accent}")
            else:
                rail.append(f"○ {name}", style=P.faint)
        return Group(Text(""), line1, rail, Text("─" * w, style=P.line))


# -- project brief ---------------------------------------------------------------


class Brief(Widget):
    """Project name and metadata on the left, planning window on the right."""

    DEFAULT_CSS = "Brief { height: auto; min-height: 2; }"

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.heading = "No project loaded"
        self.meta = "Waiting for a Microsoft Project plan"
        self.win_value = ""
        self.win_note = ""
        self.win_role = "faint"

    def set(self, name: str, meta: str, win_value: str = "", win_note: str = "",
            win_role: str = "faint") -> None:
        self.heading, self.meta = name, meta
        self.win_value, self.win_note, self.win_role = win_value, win_note, win_role
        self.refresh(layout=True)

    def render(self) -> Table:
        P = pal(self)
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1)
        grid.add_column(justify="right")
        loaded = self.win_value != ""
        grid.add_row(
            Text(self.heading, style=f"bold {P.ink if loaded else P.dim}"),
            Text(self.win_value, style=f"bold {P.accent}"),
        )
        grid.add_row(
            Text(self.meta, style=P.dim if loaded else P.faint),
            Text(self.win_note, style=getattr(P, self.win_role)),
        )
        return grid


# -- KPI tile ---------------------------------------------------------------------


class Metric(Widget):
    """One KPI tile: accent bar, caption, value, note."""

    DEFAULT_CSS = "Metric { height: 4; }"

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.title, self.value, self.detail, self.role = "", "—", "", "faint"

    def set_metric(self, title: str, value: str, detail: str = "", role: str = "accent") -> None:
        self.title, self.value, self.detail = title, value, detail
        self.role = role if value and value != "—" else "faint"
        self.refresh()

    def render(self) -> Text:
        P = pal(self)
        color = getattr(P, self.role)
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append("▎", style=color)
        t.append(f" {self.title}\n", style=f"bold {P.faint}")
        t.append("▎", style=color)
        t.append(f" {self.value or '—'}\n", style=f"bold {color}")
        t.append("▎", style=color)
        t.append(f" {self.detail}", style=P.dim)
        return t


# -- queue header / footer ------------------------------------------------------------


class QueueHeader(Widget):
    DEFAULT_CSS = "QueueHeader { height: 2; }"

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.status: tuple[str, str] | None = None  # (label, role)
        self.pieces: list[str] = []
        self.stale = False
        self.count: int | None = None

    def render(self) -> Group:
        P = pal(self)
        w = max(20, self.size.width)
        l1 = Text(no_wrap=True, overflow="ellipsis")
        l1.append("PRIORITY QUEUE", style=f"bold {P.ink}")
        if self.status:
            label, role = self.status
            l1.append("  ")
            l1.append(f" {label} ", style=f"bold {P.bg} on {getattr(P, role)}")
        if self.stale:
            l1.append("  ")
            l1.append(" STALE · press A ", style=f"bold {P.bg} on {P.warn}")
        if self.count is not None:
            right = f"{self.count} ranked"
            l1.append(" " * max(2, w - l1.cell_len - len(right)))
            l1.append(right, style=P.faint)
        l2 = Text(
            "  ·  ".join(self.pieces) if self.pieces else "Run the analysis to rank the candidates",
            style=P.dim if self.pieces else P.faint, no_wrap=True, overflow="ellipsis",
        )
        return Group(l1, l2)


class Note(Widget):
    """One quiet line under the queue: legend, capture warnings or the read-only note."""

    DEFAULT_CSS = "Note { height: 1; }"

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.kind = "readonly"
        self.detail = ""

    def render(self) -> Text:
        P = pal(self)
        t = Text(no_wrap=True, overflow="ellipsis")
        if self.kind == "legend":
            for letter, role, label in (
                ("C", "bad", "critical path"), ("R", "info", "resource conflict"),
                ("V", "warn", "needs review"), ("D", "faint", "missing data"),
            ):
                t.append(letter, style=f"bold {getattr(P, role)}")
                t.append(f" {label}   ", style=P.faint)
        elif self.kind == "warn":
            t.append("● capture warnings  ", style=f"bold {P.warn}")
            t.append(self.detail, style=P.dim)
        else:
            t.append("Read-only integration — nothing is written back to Microsoft Project", style=P.faint)
        return t


# -- hero / state view ----------------------------------------------------------------


# 3-row box-drawing wordmark font (compact, legible, no look-alike glyphs).
_LETTERS = {
    "P": ("╔═╗", "╠═╝", "╩  "),
    "R": ("╦═╗", "╠╦╝", "╩╚═"),
    "O": ("╔═╗", "║ ║", "╚═╝"),
    "J": ("  ╦", "  ║", "╚═╝"),
    "E": ("╔══", "╠═ ", "╚══"),
    "C": ("╔═╗", "║  ", "╚═╝"),
    "T": ("╔╦╗", " ║ ", " ╩ "),
    "I": (" ╦ ", " ║ ", " ╩ "),
    "L": ("╦  ", "║  ", "╩═╝"),
}
_WORD_ROWS = 3


def _word(word: str) -> tuple[str, ...]:
    return tuple(" ".join(_LETTERS[c][r] for c in word) for r in range(_WORD_ROWS))


# Large wordmark (ANSI Shadow style): solid blocks + a softer shadow tone.
_BIG = {
    'P': (
        '██████╗ ',
        '██╔══██╗',
        '██████╔╝',
        '██╔═══╝ ',
        '██║     ',
        '╚═╝     ',
    ),
    'R': (
        '██████╗ ',
        '██╔══██╗',
        '██████╔╝',
        '██╔══██╗',
        '██║  ██║',
        '╚═╝  ╚═╝',
    ),
    'O': (
        ' ██████╗ ',
        '██╔═══██╗',
        '██║   ██║',
        '██║   ██║',
        '╚██████╔╝',
        ' ╚═════╝ ',
    ),
    'J': (
        '     ██╗',
        '     ██║',
        '     ██║',
        '██   ██║',
        '╚█████╔╝',
        ' ╚════╝ ',
    ),
    'E': (
        '███████╗',
        '██╔════╝',
        '█████╗  ',
        '██╔══╝  ',
        '███████╗',
        '╚══════╝',
    ),
    'C': (
        ' ██████╗',
        '██╔════╝',
        '██║     ',
        '██║     ',
        '╚██████╗',
        ' ╚═════╝',
    ),
    'T': (
        '████████╗',
        '╚══██╔══╝',
        '   ██║   ',
        '   ██║   ',
        '   ██║   ',
        '   ╚═╝   ',
    ),
    'I': (
        '██╗',
        '██║',
        '██║',
        '██║',
        '██║',
        '╚═╝',
    ),
    'L': (
        '██╗     ',
        '██║     ',
        '██║     ',
        '██║     ',
        '███████╗',
        '╚══════╝',
    ),
}


def _mix(a: str, b: str, t: float) -> str:
    """Blend hex colour ``a`` toward ``b`` by ``t`` (0..1)."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


def _big_rows(P: Palette) -> tuple[list[Text], int]:
    """The big ProjectPilot wordmark: bright faces, dimmed shadows."""
    rows: list[Text] = []
    for r in range(6):
        row = Text(no_wrap=True)
        for word, color in (("PROJECT", P.ink), ("PILOT", P.accent)):
            shadow = _mix(color, P.bg, 0.62)
            for ch in "".join(_BIG[c][r] for c in word):
                row.append(ch, style=f"bold {color}" if ch == "█" else shadow if ch != " " else None)
            if word == "PROJECT":
                row.append(" ")
        rows.append(row)
    return rows, rows[0].cell_len


class Hero(Widget):
    """Everything that is not the queue: welcome, guided steps, spinner, errors."""

    DEFAULT_CSS = "Hero { height: 1fr; }"

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.mode = "welcome"  # welcome | ready | busy | error
        self.step = 0
        self.frame = 0
        self.title = ""
        self.detail = ""
        self.blocks: list[tuple[str, str]] = []

    def _steps(self, P: Palette) -> Text:
        t = Text()
        for i, (name, key, desc) in enumerate(STEPS):
            if i < self.step:
                t.append(f"  ✓  {name:<8}", style=P.good)
                t.append(f" {key} ", style=f"{P.faint} on {P.raised}")
                t.append(f"  {desc}\n", style=P.dim)
            elif i == self.step:
                t.append(f"  ▶  {name:<8}", style=f"bold {P.accent}")
                t.append(f" {key} ", style=f"bold {P.bg} on {P.accent}")
                t.append(f"  {desc}", style=f"bold {P.ink}")
                t.append("   ← next\n", style=P.accent)
            else:
                t.append(f"  ○  {name:<8}", style=P.faint)
                t.append(f" {key} ", style=f"{P.faint} on {P.surface}")
                t.append(f"  {desc}\n", style=P.faint)
        return t

    def render(self) -> Align | Group:
        P = pal(self)
        h = max(3, self.size.height)
        w = self.size.width
        if self.mode == "busy":
            spin = SPINNER[self.frame % len(SPINNER)]
            return Align.center(
                Group(
                    Text(f"{spin}  {self.title}", style=f"bold {P.accent}"),
                    Text(""),
                    Text(self.detail, style=P.dim),
                ),
                vertical="middle", height=h,
            )
        if self.mode == "error":
            grid = Table.grid(padding=(0, 2))
            grid.add_column(width=5, justify="right")
            grid.add_column(ratio=1)
            roles = {"WHAT": P.bad, "WHY": P.dim, "NEXT": P.accent}
            for label, text in self.blocks:
                grid.add_row(Text(label, style=f"bold {roles.get(label, P.faint)}"), Text(text, style=P.ink if label != "WHY" else P.dim))
                grid.add_row("", "")
            return Group(
                Text(f"✖  {self.title}", style=f"bold {P.bad}"),
                Text(""),
                grid,
            )

        parts: list[Any] = []
        if self.mode == "welcome":
            big, big_w = _big_rows(P)
            pa, pb = _word("PROJECT"), _word("PILOT")
            small_w = len(pa[0]) + 1 + len(pb[0])
            if w >= big_w + 4 and h >= 19:
                parts += big
                parts.append(Text(""))
            elif w >= small_w + 6:
                for r in range(_WORD_ROWS):
                    row = Text(no_wrap=True)
                    row.append(pa[r], style=f"bold {P.ink}")
                    row.append(" ")
                    row.append(pb[r], style=f"bold {P.accent}")
                    parts.append(row)
                parts.append(Text("─" * small_w, style=P.line, justify="center"))
            else:
                parts.append(Text.assemble(
                    ("✦ ", f"bold {P.accent}"),
                    ("Project", f"bold {P.ink}"),
                    ("Pilot", f"bold {P.accent}"),
                    justify="center",
                ))
                parts.append(Text(""))
            parts += [
                Text("Which activities deserve attention first — and why?", style=f"italic {P.dim}", justify="center"),
                Text(""),
            ]
        else:
            parts += [Text("READY TO PLAN", style=f"bold {P.faint}"), Text("")]
        steps = self._steps(P)
        parts.append(Align.center(steps) if self.mode == "welcome" else steps)
        foot = Text(justify="center")
        if self.mode == "welcome":
            foot.append("No plan at hand? ", style=P.faint)
            foot.append(" S ", style=f"{P.dim} on {P.raised}")
            foot.append(" opens the bundled sample project.", style=P.faint)
        parts.append(foot)
        return Align.center(Group(*parts), vertical="middle", height=h)


# -- inspector -------------------------------------------------------------------------

_CRIT = (
    ("SP", "Pressure", "accent"),
    ("SI", "Successors", "info"),
    ("CD", "Consequence", "violet"),
)


def _pressure_word(P: Palette, value: float | None) -> tuple[str, str]:
    if value is None:
        return "unknown — no slack data was reported", P.warn
    if value >= 2 / 3:
        return f"high (SP {value:.3f})", P.bad
    if value >= 1 / 3:
        return f"moderate (SP {value:.3f})", P.warn
    return f"low (SP {value:.3f})", P.dim


def _reason_text(reason: Any) -> str:
    key = getattr(reason, "code", None) or getattr(reason, "value", None) or reason
    key = str(key)
    return _REASON_LABELS.get(key, key.replace("_", " ").capitalize())


def build_inspector(P: Palette, width: int, ranked: Any, task: Any, evaluation: Any,
                    has_result: bool, offline: bool) -> Group:
    w = max(30, width)
    out: list[Any] = []
    unscored = ranked.score is None

    # title block
    top = Text()
    top.append(f" #{ranked.rank} ", style=f"bold {P.bg} on {P.accent}")
    if ranked.is_critical is True:
        top.append("   ▲ CRITICAL PATH", style=f"bold {P.bad}")
    out += [top, Text(""), Text(ranked.task_name, style=f"bold {P.ink}")]
    if task is not None:
        pct = f"{task.progress * 100:.0f}%"
        status = ("completed" if task.progress >= 1.0
                  else f"in progress ({pct})" if task.progress > 0 else "not started")
        dates = (f"{task.start.isoformat()} → {task.finish.isoformat()}  ·  {task.duration_days:.1f}d"
                 if task.start is not None and task.finish is not None else "undated")
        out.append(Text(f"{status}  ·  {dates}", style=P.dim))
    out.append(Text(""))

    # score
    out.append(heading(P, "PRIORITY INDEX"))
    if unscored:
        out.append(Text("⚠ unscored — missing schedule data", style=f"bold {P.warn}"))
    else:
        score = ranked.total_score
        s = Text()
        s.append(f"{score:.1f}", style=f"bold {P.accent}")
        s.append("  / 100", style=P.faint)
        out.append(s)
        out.append(hbar(score / 100, w, P.accent, P.track, P.faint))
    out.append(Text(""))

    # composition + breakdown
    out.append(heading(P, "WHERE THE SCORE COMES FROM"))
    contributions = (None, None, None) if unscored else tuple(ranked.criterion_contributions)
    values = (ranked.schedule_pressure, ranked.successor_impact, ranked.consequence_of_delay)
    if not unscored and any(c for c in contributions):
        stack = Text()
        cells = 0
        for (tag, _n, role), c in zip(_CRIT, contributions):
            n = round(((c or 0.0) / 100.0) * w)
            stack.append("█" * n, style=f"{getattr(P, role)} on {P.track}")
            cells += n
        stack.append(" " * max(0, w - cells), style=f"on {P.track}")
        out.append(stack)
        legend = Text()
        for (tag, _n, role), c in zip(_CRIT, contributions):
            legend.append("■ ", style=getattr(P, role))
            legend.append(f"{tag} {c or 0.0:.1f}    ", style=P.dim)
        out.append(legend)
        out.append(Text(""))
    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=2)
    grid.add_column(width=11)
    grid.add_column(width=12)
    grid.add_column(width=5, justify="right")
    grid.add_column(width=7, justify="right")
    for (tag, name, role), v, c in zip(_CRIT, values, contributions):
        color = getattr(P, role)
        if v is None or unscored:
            grid.add_row(Text(tag, style=f"bold {color}"), Text(name, style=P.dim),
                         hbar(None, 12, color, P.track, P.faint),
                         Text("—", style=P.faint), Text("no data", style=f"italic {P.warn}"))
        else:
            grid.add_row(Text(tag, style=f"bold {color}"), Text(name, style=P.dim),
                         hbar(v, 12, color, P.track, P.faint),
                         Text(f"{v:.3f}", style=P.ink),
                         Text(f"+{(c or 0.0):.1f}", style=P.faint))
    out += [grid, Text("")]

    # signals
    out.append(heading(P, "DECISION SIGNALS"))
    sig = Text()
    word, color = _pressure_word(P, ranked.schedule_pressure)
    sig.append(" ▲ ", style=f"bold {color}")
    sig.append(f"Schedule pressure is {word}\n", style=color)
    if ranked.successor_count is None:
        sig.append(" ↳ ", style=f"bold {P.warn}")
        sig.append("Successor impact unknown — flagged for review\n", style=P.warn)
    elif ranked.successor_count > 0:
        sig.append(" ↳ ", style=f"bold {P.info}")
        sig.append(f"{ranked.successor_count} downstream activities depend on this\n", style=P.info)
    else:
        sig.append(" ↳ ", style=f"bold {P.dim}")
        sig.append("No downstream dependencies\n", style=P.dim)
    if ranked.consequence_source == "evaluated":
        conf = (f" (confidence {evaluation.confidence:.2f})"
                if evaluation is not None and getattr(evaluation, "confidence", None) is not None else "")
        sig.append(" ◆ ", style=f"bold {P.violet}")
        sig.append(f"Consequence evaluated by LIVE JEV{conf}\n", style=P.violet)
    else:
        sig.append(" ◇ ", style=f"bold {P.warn}")
        sig.append("Consequence PROVISIONAL 0.500 — not a live JEV result\n", style=P.warn)
    rng = ranked.score_range
    if not unscored and rng is not None and rng.minimum != rng.maximum:
        sig.append(f"   └ score sensitivity {rng.minimum:.1f} – {rng.maximum:.1f}\n", style=P.faint)
    if ranked.resource_warning:
        sig.append(" ⚑ ", style=f"bold {P.info}")
        sig.append("Shared resource: " + ", ".join(ranked.shared_resource_names) + "\n", style=P.info)
    elif has_result:
        sig.append(" ✓ ", style=f"bold {P.dim}")
        sig.append("No resource conflict in this session\n", style=P.dim)
    if ranked.total_slack_days is None:
        sig.append(" ▲ ", style=f"bold {P.warn}")
        sig.append("Missing schedule data — flagged for review\n", style=P.warn)
    sig.rstrip()
    out += [sig, Text("")]

    reasons = list(ranked.review_reasons or [])
    if reasons:
        out.append(heading(P, "REVIEW FLAGS"))
        rt = Text()
        for r in reasons:
            rt.append(" ● ", style=P.warn)
            rt.append(_reason_text(r) + "\n", style=P.dim)
        rt.rstrip()
        out += [rt, Text("")]

    # facts
    out.append(heading(P, "FACTS"))
    facts = Table.grid(padding=(0, 2))
    facts.add_column(width=10)
    facts.add_column()
    slack = f"{ranked.total_slack_days:.1f} working days" if ranked.total_slack_days is not None else "unknown"
    crit = {True: "yes", False: "no"}.get(ranked.is_critical, "not reported")
    succ = str(ranked.successor_count) if ranked.successor_count is not None else "unknown"
    for label, val, style in (
        ("SLACK", slack, P.ink),
        ("CRITICAL", crit, P.bad if ranked.is_critical else P.ink),
        ("SUCCESSORS", succ, P.ink),
    ):
        facts.add_row(Text(label, style=f"bold {P.faint}"), Text(val, style=style))
    if task is None:
        facts.add_row(Text("ROW", style=f"bold {P.faint}"), Text("not in the loaded snapshot", style=P.warn))
    out += [facts, Text("")]

    notes = (task.notes if task is not None else None) or None
    n = Text()
    n.append("NOTES  ", style=f"bold {P.faint}")
    n.append(_clip(notes, 180) if notes else "none", style=P.dim if notes else P.faint)
    out += [n, Text("")]

    # next action
    if unscored:
        action = ("Restore the missing schedule data in Microsoft Project and press R "
                  "to re-capture; the activity stays unscored until then.")
    else:
        acts: list[str] = []
        if ranked.resource_warning:
            acts.append("Coordinate shared resource (" + ", ".join(ranked.shared_resource_names)
                        + ") before committing this window")
        if ranked.consequence_source != "evaluated":
            acts.append("Press J then A to evaluate consequence live, or validate provisional 0.5 with the team"
                        if offline else
                        "Consequence could not be evaluated (e.g. no notes) — validate provisional value with the team")
        if ranked.review_reasons:
            acts.append("Clear review flags before acting on it")
        if not acts:
            acts.append("Use this ranking position in the period decision; no review items are attached")
        action = " · ".join(acts[:2]) + "."
    card = Group(Text("NEXT ACTION", style=f"bold {P.accent}"), Text(action, style=P.ink))
    out.append(Padding(card, (1, 2), style=f"on {P.raised}"))
    return Group(*out)


class Inspector(Widget):
    DEFAULT_CSS = "Inspector { height: auto; }"

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.model: tuple | None = None

    def show(self, ranked, task, evaluation, has_result: bool, offline: bool) -> None:
        self.model = (ranked, task, evaluation, has_result, offline)
        self.refresh(layout=True)

    def clear(self) -> None:
        self.model = None
        self.refresh(layout=True)

    def render(self) -> Group | Text:
        P = pal(self)
        if self.model is None:
            return Group(Text("ACTIVITY INSPECTOR", style=f"bold {P.faint}"), Text(""),
                         Text("Select an activity in the queue.\n↑ ↓ to browse.", style=P.faint))
        return build_inspector(P, self.size.width, *self.model)


# -- timeline ----------------------------------------------------------------------------


class Timeline(Widget):
    """Compact Gantt strip over the planning window with a today marker."""

    DEFAULT_CSS = "Timeline { height: auto; max-height: 14; }"

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.message = "No project loaded"
        self.data: dict | None = None

    def set_empty(self, message: str) -> None:
        self.message, self.data = message, None
        self.refresh(layout=True)

    def render_schedule(self, labels, starts, finishes, window_start, window_end, *,
                        completed=None, progress=None, today=None, title="TIMELINE") -> None:
        self.data = dict(labels=labels, starts=starts, finishes=finishes, ws=window_start,
                         we=window_end, completed=completed, progress=progress,
                         today=today, title=title)
        self.refresh(layout=True)

    def render(self) -> Group | Text:
        P = pal(self)
        d = self.data
        if d is None:
            return Text(f"TIMELINE  {self.message}", style=P.faint)
        width = max(50, self.size.width)
        label_w = max(16, min(30, width * 28 // 100))
        track_w = max(12, width - label_w - 11 - 6 - 3)
        total = max(1, (d["we"] - d["ws"]).days)

        head = Text()
        head.append("TIMELINE", style=f"bold {P.ink}")
        head.append(f"  {d['title'].split('—')[-1].strip()}", style=P.faint)
        today = d["today"]
        today_col = None
        if today is not None and d["ws"] <= today <= d["we"]:
            today_col = min(track_w - 1, int(((today - d["ws"]).days / total) * track_w))
            head.append(f"   ┊ today {today.isoformat()}", style=P.warn)

        ruler = [" "] * track_w
        for frac in (0.0, 1 / 3, 2 / 3, 1.0):
            day = d["ws"] + dt.timedelta(days=round(total * frac))
            lab = day.strftime("%m-%d")
            pos = min(track_w - len(lab), int(frac * (track_w - len(lab))))
            for k, ch in enumerate(lab):
                ruler[pos + k] = ch
        rl = Text(" " * (label_w + 1 + 11 + 1))
        rl.append("".join(ruler), style=P.faint)

        lines: list[Any] = [head, rl]
        for i, label in enumerate(d["labels"]):
            start = d["starts"][i] if i < len(d["starts"]) else None
            finish = d["finishes"][i] if i < len(d["finishes"]) else None
            line = Text(no_wrap=True)
            line.append(str(label)[:label_w].ljust(label_w) + " ", style=P.ink)
            if start is None or finish is None:
                line.append("undated", style=P.faint)
                lines.append(line)
                continue
            pct = 0.0
            prog = d["progress"]
            if prog is not None and i < len(prog) and prog[i] is not None:
                pct = max(0.0, min(1.0, float(prog[i])))
            comp = d["completed"]
            done = bool(comp[i]) if comp is not None and i < len(comp) else pct >= 1.0
            line.append(f"{start.strftime('%m-%d')}→{finish.strftime('%m-%d')}", style=P.faint)
            line.append(" ")
            s_off = max(0, min(total, (start - d["ws"]).days))
            f_off = max(s_off + 1, min(total, (finish - d["ws"]).days + 1))
            b0 = int(s_off / total * track_w)
            b1 = min(track_w, max(b0 + 2, int(f_off / total * track_w)))
            span = b1 - b0
            filled = span if done else round(span * pct)
            cells: list[tuple[str, str]] = []
            for c in range(track_w):
                if c < b0 or c >= b1:
                    cells.append(("┈", P.line))
                elif c - b0 < filled:
                    cells.append(("█", P.good if done else P.info))
                else:
                    cells.append(("▒", P.accent if pct == 0 else P.dim))
            if today_col is not None and cells[today_col][0] == "┈":
                cells[today_col] = ("┊", P.warn)
            for ch, st in cells:
                line.append(ch, style=st)
            line.append("  DONE" if done else f"  {round(pct * 100):>3}%",
                        style=f"bold {P.good}" if done else (P.info if pct > 0 else P.faint))
            lines.append(line)
        return Group(*lines)


# -- key bar -------------------------------------------------------------------------------

KEYS = (
    ("L", "load", "Load"), ("P", "period", "Period"), ("C", "confirm", "Confirm"),
    ("A", "analyze", "Analyze"), ("J", "toggle_jev", "JEV mode"), ("R", "reload", "Re-capture"),
    ("T", "toggle_timeline", "Timeline"), ("S", "sample", "Sample"),
    ("?", "help", "Help"), ("Q", "quit", "Quit"),
)
_NEXT_KEY = ("L", "P", "C", "A", None)


class KeyBar(Widget):
    """Context hint plus clickable key chips; the next workflow key glows."""

    DEFAULT_CSS = "KeyBar { height: 3; padding: 0 2; }"

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.step = 0
        self.loaded = False
        self.hint = ""
        self.welcome = False

    def _enabled(self, key: str) -> bool:
        if key in ("P", "R", "T"):
            return self.loaded
        if key == "C":
            return self.step >= 2
        if key == "A":
            return self.step >= 3
        return True

    def render(self) -> Group:
        P = pal(self)
        hint = Text(no_wrap=True, overflow="ellipsis")
        hint.append("▸ ", style=f"bold {P.accent}")
        if self.welcome:
            hint.append("Read-only integration — nothing is written back to Microsoft Project", style=P.faint)
        else:
            hint.append(self.hint, style=P.dim)
        chips = Text()
        compact = self.size.width < 136
        nxt = _NEXT_KEY[min(self.step, 4)]
        for key, action, label in KEYS:
            click = Style(meta={"@click": f"app.{action}"})
            on = self._enabled(key)
            if key == nxt:
                chips.append(f" {key} ", style=Style.parse(f"bold {P.bg} on {P.accent}") + click)
                chips.append(f" {label}", style=Style.parse(f"bold {P.ink}") + click)
            else:
                chips.append(f" {key} ", style=Style.parse(f"{P.dim if on else P.faint} on {P.raised if on else P.surface}") + click)
                if not compact or key in ("L", "P", "C", "A"):
                    chips.append(f" {label}", style=Style.parse(P.dim if on else P.faint) + click)
            chips.append("   " if not compact else "  ")
        return Group(hint, Text(""), chips)
