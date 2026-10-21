"""Deterministic priority scoring: criteria + weighted score, no JEV, no ranking.

Pipeline position (this module is the middle of it):

    PlanningSession + ScheduleAnalysis + ScoringParameters
            + precomputed Consequence-of-Delay input
            ↓
    TaskPriorityScore per candidate (0..100 index)

Deliberate boundaries:
- JEV is NOT called nor referenced here. Consequence of Delay (CD)
  arrives as an already-normalized value in [0, 1]; scoring only
  validates that range. A future level→CD mapping lives outside this
  module (the parameters carry ``consequence_max_level`` only so the
  eventual mapping can read the configured level count).
- NO ranking/sorting: this module computes per-task scores; ordering is
  a consuming service's job.
- Criticality (``is_critical``) is exposed for future UI badges but has
  ZERO effect on the numeric score — criticality and Schedule Pressure
  would otherwise double-count the same schedule signal.
- Resource conflicts stay a separate warning layer; this module never
  imports them.

Formulas (documented, deterministic; H = horizon working days):

- Schedule Pressure   SP = 1 - min(max(slack, 0), H) / H
  negative/zero slack → 1.0; slack >= H → 0.0. ``is_critical`` is
  never used. Missing slack → SP is unavailable, never invented.
- Successor Impact    SI = min(successor_count, K) / K
  direct successors only; no candidate-relative normalization: adding
  or removing other candidates never changes any value.
- Consequence         CD in [0, 1] — a validated input, not an output.
- Score  score = 100 * (w_schedule*SP + w_successor*SI + w_consequence*CD)

The score is a prioritization INDEX — not a probability, percentage
chance, calibrated utility, or prediction.

Missing data rules (no invented observations, no weight re-normalization):
- SP or SI unavailable → that value and its contribution are None and
  NO total score is computed (None); needs_review marks it. A missing
  input is never silently treated as zero — that would understate
  pressure and bias candidates against everything that lacks data.
- CD unavailable → a provisional NEUTRAL 0.5 is used and exposed as
  ``consequence_source="provisional"`` (never "evaluated"), with the
  exact score range that CD=0 vs. CD=1 would still allow. Confidence
  is never multiplied into the score.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from project_decision.application.schedule_analysis import ScheduleAnalysis, TaskSchedule
from project_decision.domain.planning_period import PlanningPeriod
from project_decision.domain.planning_session import PlanningSession

_WEIGHT_TOLERANCE = 1e-9
NEUTRAL_CONSEQUENCE = 0.5  # the only provisional fill-in of this module


@dataclass(frozen=True)
class ScoringParameters:
    """Scoring configuration; H comes from the PlanningPeriod by default.

    ``horizon`` is an explicit H override (what-if analysis); when None
    the session's ``PlanningPeriod.working_days`` is used — the period
    length is never separately re-declared by callers. Weights are
    validated (non-negative, sum 1.0 within float tolerance) and
    REJECTED when invalid — never silently normalized, and never
    re-normalized per task when a criterion is missing.
    """

    successor_cap: int = 5
    consequence_max_level: int = 3  # highest input level; read by the future level→CD mapping, not by the formula
    schedule_pressure_weight: float = 0.40
    successor_impact_weight: float = 0.20
    consequence_of_delay_weight: float = 0.40
    horizon: float | None = None

    def __post_init__(self) -> None:
        if self.successor_cap < 1:
            raise ValueError("successor_cap must be >= 1")
        if self.consequence_max_level < 1:
            raise ValueError("consequence_max_level must be >= 1")
        if self.horizon is not None and self.horizon <= 0:
            raise ValueError("horizon must be positive when given")
        weights = (
            self.schedule_pressure_weight,
            self.successor_impact_weight,
            self.consequence_of_delay_weight,
        )
        if any(weight < 0 for weight in weights):
            raise ValueError("criterion weights must be non-negative")
        if abs(sum(weights) - 1.0) > _WEIGHT_TOLERANCE:
            raise ValueError(
                "criterion weights must sum to 1.0 (they are never normalized silently)"
            )


@dataclass(frozen=True)
class ScoreRange:
    """Score interval owed to a provisional CD: the CD=0 vs. CD=1 results."""

    minimum: float
    maximum: float


@dataclass(frozen=True)
class TaskPriorityScore:
    """One candidate's deterministic score with full explanation.

    ``None`` criterion values mean exactly UNAVAILABLE (e.g. no slack
    reported) — never zero. ``total_score`` is None whenever SP or SI is
    unavailable (weights are not re-normalized). When the CD was
    provisional, ``total_score`` was computed WITH the neutral 0.5 and
    ``score_range`` delimits what a real evaluation could still change.
    ``is_critical`` is display-only passthrough; it never changes the
    numbers above.
    """

    task_id: str
    schedule_pressure: float | None
    successor_impact: float | None
    consequence_of_delay: float
    consequence_source: str  # "evaluated" | "provisional"
    schedule_contribution: float | None
    successor_contribution: float | None
    consequence_contribution: float | None
    total_score: float | None
    needs_review: bool
    review_reasons: tuple[str, ...]
    score_range: ScoreRange | None = None
    is_critical: bool | None = None  # display-only passthrough


def schedule_pressure(slack: float | None, horizon: float) -> float | None:
    """SP = 1 - min(max(slack, 0), H)/H; None when the slack is unavailable."""
    if slack is None or horizon <= 0:
        return None
    return 1.0 - min(max(slack, 0.0), horizon) / horizon


def successor_impact(successor_count: int | None, cap: int) -> float | None:
    """SI = min(direct successor count, K)/K; None when unavailable."""
    if successor_count is None:
        return None
    return min(successor_count, cap) / cap


def _validated_consequence(value: float | None) -> float | None:
    """Return the evaluated CD, or None when it was not provided."""
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"consequence_of_delay must be a number in [0, 1], not {value!r}")
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"consequence_of_delay must be in [0, 1], not {value!r}")
    return float(value)


def score_task(
    task_id: str,
    period: PlanningPeriod,
    task_schedule: TaskSchedule | None,
    consequence_of_delay: float | None = None,
    parameters: ScoringParameters | None = None,
) -> TaskPriorityScore:
    """Score ONE task; deterministic, pure, side-effect free.

    ``task_id`` is explicit because ``task_schedule`` may legitimately
    be absent (a state/schedule mismatch) — that is data-quality
    information, never a fabricated schedule. ``consequence_of_delay``
    None means NOT evaluated (the neutral 0.5 is applied and flagged).
    """
    params = parameters if parameters is not None else ScoringParameters()
    horizon = params.horizon if params.horizon is not None else period.working_days

    reasons: list[str] = []
    if task_schedule is None:
        schedule_pressure_value: float | None = None
        successor_impact_value: float | None = None
        is_critical: bool | None = None
        reasons.append("no schedule-analysis data for this task")
    else:
        schedule_pressure_value = schedule_pressure(
            task_schedule.total_slack_days, horizon
        )
        successor_impact_value = successor_impact(
            task_schedule.successor_count, params.successor_cap
        )
        is_critical = task_schedule.is_critical
        if schedule_pressure_value is None:
            if task_schedule.total_slack_days is None:
                reasons.append(
                    "schedule pressure is unavailable: no slack value was "
                    "reported (total_slack_days missing)"
                )
            else:
                reasons.append(
                    "schedule pressure is unavailable: the period has no "
                    "working days (H = 0)"
                )
        if successor_impact_value is None:
            reasons.append("successor impact is unavailable: no successor data")

    evaluated_consequence = _validated_consequence(consequence_of_delay)
    provisional = evaluated_consequence is None
    consequence_value = (
        NEUTRAL_CONSEQUENCE if provisional else evaluated_consequence
    )
    if provisional:
        reasons.append(
            "consequence of delay was not evaluated; a provisional neutral "
            f"value ({NEUTRAL_CONSEQUENCE}) is used"
        )

    schedule_contribution = (
        100.0 * params.schedule_pressure_weight * schedule_pressure_value
        if schedule_pressure_value is not None
        else None
    )
    successor_contribution = (
        100.0 * params.successor_impact_weight * successor_impact_value
        if successor_impact_value is not None
        else None
    )
    consequence_contribution = (
        100.0 * params.consequence_of_delay_weight * consequence_value
    )

    score_range: ScoreRange | None = None
    if (
        provisional
        and schedule_pressure_value is not None
        and successor_impact_value is not None
    ):
        fixed = (
            100.0 * params.schedule_pressure_weight * schedule_pressure_value
            + 100.0 * params.successor_impact_weight * successor_impact_value
        )
        consequence_span = 100.0 * params.consequence_of_delay_weight
        score_range = ScoreRange(minimum=fixed, maximum=fixed + consequence_span)

    total: float | None = None
    if schedule_pressure_value is not None and successor_impact_value is not None:
        total = (
            schedule_contribution + successor_contribution + consequence_contribution
        )

    return TaskPriorityScore(
        task_id=task_id,
        schedule_pressure=schedule_pressure_value,
        successor_impact=successor_impact_value,
        consequence_of_delay=consequence_value,
        consequence_source="provisional" if provisional else "evaluated",
        schedule_contribution=schedule_contribution,
        successor_contribution=successor_contribution,
        consequence_contribution=consequence_contribution,
        total_score=total,
        needs_review=bool(reasons),
        review_reasons=tuple(reasons),
        score_range=score_range,
        is_critical=is_critical,
    )


def score_planning_session(
    session: PlanningSession,
    schedule_analysis: ScheduleAnalysis,
    consequence_of_delay: float | Mapping[str, float] | None = None,
    parameters: ScoringParameters | None = None,
) -> tuple[TaskPriorityScore, ...]:
    """Score every candidate of a session, in candidate order (deterministic).

    ``consequence_of_delay`` is either a single evaluated CD for all
    candidates or a per-task-id mapping; a candidate without an entry
    counts as NOT evaluated (provisional 0.5). Nothing is mutated and
    no candidate-relative normalization happens.
    """
    results: list[TaskPriorityScore] = []
    for candidate_id in session.candidate_task_ids:
        if isinstance(consequence_of_delay, Mapping):
            cd: float | None = consequence_of_delay.get(candidate_id)
        else:
            cd = consequence_of_delay
        results.append(
            score_task(
                task_id=candidate_id,
                period=session.planning_period,
                task_schedule=schedule_analysis.schedules.get(candidate_id),
                consequence_of_delay=cd,
                parameters=parameters,
            )
        )
    return tuple(results)
