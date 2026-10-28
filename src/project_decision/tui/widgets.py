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

from rich.text import Text
from textual.widgets import Static

# -- design tokens (shared with app.py via CSS literals) -----------------------
ACCENT = "#55d6be"  # teal (primary accent, reference token)
BLUE = "#8ab4ff"  # informational accent (resource flags)
WARN = "#ffbd69"  # warning / provisional
ERROR = "#ff8294"  # error / bad / critical
GOOD = "#55d6be"  # positive = the primary accent
TEXT = "#e7edf5"  # primary text
DIM = "#a8b6c9"  # secondary text
FAINT = "#64748b"  # tertiary text
NAVY = "#0d1117"  # deep screen background
PANEL = "#131922"  # panel background
CARD = "#18202c"  # card background
BORDER = "#2a3546"  # rounded border color
HEADER_BG = "#161d27"  # header/footer background
BAR_BG = "#222d3d"  # empty bar track

BAR_WIDTH = 16


class StatCard(Static):
    """One reference-style metric tile: LABEL / big value / note."""

    def set_metric(
        self,
        title: str,
        value: str,
        detail: str = "",
        color: str = ACCENT,
    ) -> None:
        text = Text()
        text.append(f"▸ {title}\n", style=f"bold {FAINT}")
        if value and value != "—":
            text.append(f"  {value}", style=f"bold {color}")
        else:
            text.append("  —", style=f"bold {FAINT}")
        if detail:
            text.append(f"\n  {detail}", style=DIM)
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
                    ("━" * BAR_WIDTH, BAR_BG),
                    ("  no data — flagged", f"italic {WARN}"),
                )
            )
            return

        bar_color = color
        if label == "SP" and value >= 0.67:
            bar_color = ERROR
        elif label == "SI" and value >= 0.67:
            bar_color = BLUE
        elif label == "CD":
            bar_color = WARN if value == 0.5 else GOOD

        text = Text()
        text.append(f" {label:<4}", style=f"bold {TEXT}")
        filled = max(0, min(BAR_WIDTH, round(value * BAR_WIDTH)))
        text.append("█" * filled, style=bar_color)
        text.append("░" * (BAR_WIDTH - filled), style=BAR_BG)
        contribution_text = (
            "+0.0 pts" if contribution is None else f"+{contribution:>4.1f} pts"
        )
        text.append(f"  {value:.3f} ", style=TEXT)
        text.append(f"({contribution_text})", style=DIM)
        self.update(text)


class TimelineChart(Static):
    """A compact textual Gantt strip over the planning period."""

    TRACK_WIDTH = 28

    def render_schedule(
        self,
        labels,
        starts,
        finishes,
        window_start,
        window_end,
        *,
        completed=None,
        progress=None,
        today=None,
        title="TIMELINE",
    ) -> None:
        total_days = max(1, (window_end - window_start).days)
        lines = []

        header = Text()
        header.append(f" ▸ {title} ", style=f"bold {ACCENT}")
        header.append(
            f" ({window_start.isoformat()} → {window_end.isoformat()})",
            style=DIM,
        )
        if today is not None and window_start <= today <= window_end:
            header.append(f"   ● today: {today.isoformat()}", style=f"bold {WARN}")
        lines.append(header)

        for index, label in enumerate(labels):
            start = starts[index] if index < len(starts) else None
            finish = finishes[index] if index < len(finishes) else None
            line = Text()
            clean_label = str(label)[:32].ljust(32)
            line.append(f"   {clean_label} ", style=TEXT)

            if start is None or finish is None:
                line.append("  ── undated ──", style=FAINT)
                lines.append(line)
                continue

            pct = 0.0
            if (
                progress is not None
                and index < len(progress)
                and progress[index] is not None
            ):
                pct = max(0.0, min(1.0, float(progress[index])))
            is_done = (
                bool(completed[index])
                if completed is not None and index < len(completed)
                else (pct >= 1.0)
            )

            date_str = f"{start.strftime('%m-%d')}→{finish.strftime('%m-%d')}"
            line.append(f" {date_str}  ", style=FAINT)

            # Calculate Gantt bar position within window
            s_offset = max(0, min(total_days, (start - window_start).days))
            f_offset = max(
                s_offset + 1, min(total_days, (finish - window_start).days + 1)
            )

            bar_start = int((s_offset / total_days) * self.TRACK_WIDTH)
            bar_end = max(
                bar_start + 2, int((f_offset / total_days) * self.TRACK_WIDTH)
            )
            bar_end = min(self.TRACK_WIDTH, bar_end)
            span = bar_end - bar_start
            done_chars = round(span * pct)
            todo_chars = span - done_chars

            line.append("·" * bar_start, style=BAR_BG)
            if is_done:
                line.append("█" * span, style=GOOD)
            else:
                if done_chars > 0:
                    line.append("█" * done_chars, style=BLUE)
                if todo_chars > 0:
                    line.append("▒" * todo_chars, style=ACCENT if pct == 0 else DIM)
            line.append("·" * (self.TRACK_WIDTH - bar_end), style=BAR_BG)

            status_text = " DONE" if is_done else f" {round(pct * 100):>3}%"
            status_style = f"bold {GOOD}" if is_done else (BLUE if pct > 0 else FAINT)
            line.append(status_text, style=status_style)
            lines.append(line)

        out = Text("\n").join(lines)
        self.update(out)
