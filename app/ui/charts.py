"""Chart builders for the decision dashboard (pure data → figure, no st).

Charts are EXPLANATION, not decoration: they plot values the
application layer already produced (priority scores by rank, criterion
contributions of one ranked task). No scoring, ranking, or formula
logic exists here — missing values are skipped, never zero-filled
unless the chart explicitly needs a bar (contributions must exist to
be drawn).
"""

from __future__ import annotations

import plotly.graph_objects as go

from project_decision.application.priority_ranking import RankedTask

from app.ui import components

# Keep the ranking chart readable when many activities are ranked; the
# table below always shows ALL of them. Display-only truncation.
MAX_RANKING_BARS = 15

_LEVELS = ("Schedule Pressure", "Successor Impact", "Consequence of Delay")

# Presentation colors for the existing row states (see components.row_state).
_STATE_COLORS = {
    "OK": "#4a5568",
    "REVIEW": "#d97706",
    "RESOURCE WARNING": "#b45309",
    "UNSCORED": "#94a3b8",
}


def ranking_figure(ranked_tasks: tuple[RankedTask, ...]):
    """Priority scores in rank order — the actual ranking, nothing else."""
    scored = [
        one for one in ranked_tasks
        if one.total_score is not None
    ]
    if not scored:
        return None, None
    shown = scored[:MAX_RANKING_BARS]
    figure = go.Figure(
        data=go.Bar(
            x=[f"#{one.rank} {one.task_name}" for one in shown],
            y=[one.total_score for one in shown],
            marker=dict(color=[_STATE_COLORS.get(components.row_state(one), "#4a5568")
                               for one in shown]),
        )
    )
    figure.update_layout(
        title=f"Priority score by rank (0–100) — top {len(shown)} "
              f"of {len(scored)} scored activities",
        yaxis=dict(range=[0, 100]),
        height=340,
        margin=dict(t=48, b=10, l=10, r=10),
    )
    return figure, None


def ranking_note(ranked_tasks: tuple[RankedTask, ...]) -> str | None:
    """Display note when the chart shows only the top items."""
    scored = sum(1 for one in ranked_tasks if one.total_score is not None)
    if scored <= MAX_RANKING_BARS:
        return None
    return (
        f"Chart shows the top {MAX_RANKING_BARS} of {scored} scored "
        "activities; the complete ranking is in the table below."
    )


def contribution_figure(ranked: RankedTask):
    """Criterion contributions for the selected task (existing values only)."""
    score = ranked.score
    if score is None:
        return None
    contributions = (score.schedule_contribution,
                     score.successor_contribution,
                     score.consequence_contribution)
    points = [
        (name, contribution)
        for name, contribution in zip(_LEVELS, contributions)
        if contribution is not None
    ]
    if not points:
        return None
    figure = go.Figure(
        data=go.Bar(
            x=[name for name, _ in points],
            y=[contribution for _, contribution in points],
            marker=dict(color="#4a5568"),
        )
    )
    figure.update_layout(
        title=f"Why #{ranked.rank} {ranked.task_name} scores "
              f"{components.score_text(ranked.total_score)}/100 "
              "— criterion contributions (100 × Σ weight × value)",
        height=320,
        margin=dict(t=48, b=10, l=10, r=10),
    )
    return figure
