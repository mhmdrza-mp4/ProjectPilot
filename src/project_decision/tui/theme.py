"""Design tokens for the ProjectPilot terminal dashboard.

Three hand-tuned palettes ("cockpit" ember on graphite, "glacier" cool
blue, "daylight" light). One source of truth: Rich renderables read the
active ``Palette`` through :func:`pal`, and the Textual stylesheet reads
the very same values through ``$pp-*`` CSS variables.

Pure presentation. No scoring, ranking, review, or conflict rules.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from textual.theme import Theme


@dataclass(frozen=True)
class Palette:
    name: str
    dark: bool
    bg: str  # screen background
    surface: str  # cards / KPI tiles
    panel: str  # side panel / dialogs
    raised: str  # cursor row, key chips, callouts
    line: str  # hairlines
    track: str  # empty bar track
    ink: str  # primary text
    dim: str  # secondary text
    faint: str  # tertiary text
    accent: str  # brand + primary action
    info: str  # informational / successors
    good: str  # positive
    warn: str  # provisional / review
    bad: str  # critical / error
    violet: str  # consequence / JEV


COCKPIT = Palette(
    "cockpit", True,
    bg="#0b0d11", surface="#10141a", panel="#141922", raised="#1c2230",
    line="#232a36", track="#1f2531", ink="#ece8e1", dim="#9aa3b2",
    faint="#5d6676", accent="#ff9f5a", info="#7cc4ff", good="#6ee7b7",
    warn="#f5d76e", bad="#ff6b7a", violet="#c0a4ff",
)
GLACIER = Palette(
    "glacier", True,
    bg="#0a0e17", surface="#0e1422", panel="#131b2c", raised="#1b2538",
    line="#212c42", track="#1d2740", ink="#e6edf7", dim="#93a4c0",
    faint="#56657f", accent="#6cb6ff", info="#5eead4", good="#7ee787",
    warn="#f2cc60", bad="#ff7b8a", violet="#b9a2ff",
)
DAYLIGHT = Palette(
    "daylight", False,
    bg="#f6f4ef", surface="#eeebe3", panel="#e8e4da", raised="#ddd8cb",
    line="#d2ccbd", track="#dbd6c8", ink="#1f2328", dim="#57606a",
    faint="#8c959f", accent="#c2410c", info="#0b6bcb", good="#1a7f4b",
    warn="#9a6700", bad="#cf222e", violet="#6f42c1",
)

PALETTES: dict[str, Palette] = {p.name: p for p in (COCKPIT, GLACIER, DAYLIGHT)}
DEFAULT_THEME = COCKPIT.name


def textual_theme(p: Palette) -> Theme:
    return Theme(
        name=p.name,
        primary=p.accent,
        secondary=p.info,
        warning=p.warn,
        error=p.bad,
        success=p.good,
        accent=p.violet,
        foreground=p.ink,
        background=p.bg,
        surface=p.surface,
        panel=p.panel,
        dark=p.dark,
    )


def css_variables(p: Palette) -> dict[str, str]:
    """``$pp-bg``, ``$pp-line`` ... for the stylesheet."""
    return {
        f"pp-{f.name}": getattr(p, f.name)
        for f in fields(p)
        if f.name not in ("name", "dark")
    }


def pal(widget) -> Palette:
    """The palette of the theme the app is currently using."""
    return PALETTES.get(widget.app.theme, COCKPIT)
