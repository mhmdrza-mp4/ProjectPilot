"""Custom display widgets and the design system of the terminal dashboard
(Version 2 reference: near-black background, teal/cyan accents, rounded
panels).

Pure display composition: these widgets read EXISTING immutable result
objects (``TaskPriorityScore``, ``RankedTask``, domain tasks) and render
them. They contain no scoring, ranking, review, or conflict rules, and
they never invent data — a missing value renders as an explicit "no
data" bar/label, never as zero.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

from rich.text import Text
from textual.widgets import Static

# -- design tokens (shared with app.css via CSS literals) -----------------------
ACCENT = "#55d6be"       # teal (primary accent, reference token)
BLUE = "#8ab4ff"         # informational accent (resource flags)
WARN = "#ffbd69"         # warning / provisional
ERROR = "#ff8294"        # error / bad
GOOD = "#55d6be"         # positive = the primary accent
TEXT = "#e7edf5"         # primary text
DIM = "#a8b6c9"          # secondary text
FAINT = "#718198"        # tertiary text
NAVY = "#10141b"         # screen background (reference)
PANEL = "#151b24"        # panel background
CARD = "#19212c"         # card background
BORDER = "#303b4a"       # rounded border color
HEADER_BG = "#171e28"    # header/footer background

BAR_WIDTH = 18


class StatCard(Static):
    """One reference-style metric tile: LABEL / big value / note."""

    def set_metric(self, title: str, value: str, detail: str = "") -> None:
        text = Text()
        text.append(f" {title}", style=f"bold {DIM}")
        text.append("\n ")
        if value:
            text.append(value, style=f"bold {ACCENT}")
        else:
            text.append("—", style=FAINT)
        if detail:
            text.append("\n ")
            text.append(detail, style=DIM)
        self.update(text)


class ScoreBarRow(Static):
    """One criterion bar: label, block bar, value, point contribution."""

    def set_criterion(
        self,
        label: str,
        value: float | None,
        contribution: float | None,
        color: str = ACCENT,
    ) -> None:
        if value is None:
            self.update(
                Text.assemble(
                    (f" {label:<4}", f"bold {FAINT}"),
                    ("░" * BAR_WIDTH, "#22303f"),
                    ("  no data — flagged for review", WARN),
                )
            )
            return
        text = Text()
        text.append(f" {label:<4}", style=f"bold {color}")
        filled = max(0, min(BAR_WIDTH, round(value * BAR_WIDTH)))
        text.append("█" * filled, style=color)
        text.append("░" * (BAR_WIDTH - filled), style="#22303f")
        contribution_text = (
            "+0.0 pts" if contribution is None else f"+{contribution:.1f} pts"
        )
        text.append(f"  {value:.3f} {contribution_text}", style=DIM)
        self.update(text)


class TimelineChart(Static):
    """A compact textual Gantt strip over the planning period.

    Renders the schedule the project ACTUALLY reports (real start /
    finish dates only; undated tasks are skipped, never invented). A
    blue segment shows work completed up to the capture date, teal the
    remaining span (Version 2 accent order).
    """

    TRACK_WIDTH = 56

    def render_schedule(
        self,
        labels: Sequence[str],
        starts: Sequence[date | None],
        finishes: Sequence[date | None],
        window_start: date,
        window_end: date,
        *,
        completed: Sequence[bool] | None = None,
        progress: Sequence[float] | None = None,
        today: date | None = None,
        title: str = "TIMELINE",
    ) -> None:
        total_days = max(1, (window_end - window_start).days)
        usable = self.TRACK_WIDTH - 4
        scale = usable / total_days
        lines: list[Text] = []

        header = Text()
        header.append(f" {title} ", style=f"bold {ACCENT}")
        header.append(
            f"  {window_start.isoformat()} → {window_end.isoformat()}"
            f"   ({total_days + 1} days)",
            style=DIM,
        )
        lines.append(header)

        for index, label in enumerate(labels):
            start = starts[index] if index < len(starts) else None
            finish = finishes[index] if index < len(finishes) else None
            line = Text()
            line.append(f" {label[:30]:<30}", style=TEXT)
            if start is None or finish is None:
                line.append("— undated: schedule unknown", style=DIM)
                lines.append(line)
                continue
            is_done = bool(completed[index]) if completed is not None else False
            fraction = (
                float(progress[index])
                if progress is not None and index < len(progress)
                else 0.0
            )
            left = max(0, round((start - window_start).days * scale))
            right = max(
                left,
                min(usable, round((finish - window_start).days * scale) + 1),
            )
            done_until = left + max(0, round((right - left) * fraction))
            if is_done:
                line.append("█" * max(1, right - left), style=BLUE)
            else:
                line.append(" " * left)
                count = max(0, min(done_until, usable) - left)
                if count:
                    line.append("█" * count, style=BLUE)
                tail = max(1, right - max(left, done_until))
                line.append("█" * tail, style=ACCENT)
            lines.append(line)

        axis = Text(" " * 32)
        if today is not None and window_start <= today <= window_end:
            marker = round((today - window_start).days * scale) + 1
            axis.append("▲" if marker >= 0 else "", style=WARN)
        lines.append(axis)
        lines.append(Text(" " * 31 + "┌" + "─" * usable, style=BORDER))

        legend = Text(" legend: ")
        legend.append("█", style=BLUE)
        legend.append(" completed-to-date    ", style=DIM)
        legend.append("█", style=ACCENT)
        legend.append(" remaining    ", style=DIM)
        legend.append("▲ capture date", style=DIM)
        lines.append(legend)

        # append the styled Text objects WHOLESALE: joining with str()
        # would strip every color (which once made all monochrome).
        out = Text()
        for line in lines:
            out.append(line)
            out.append("\n")
        self.update(out)
