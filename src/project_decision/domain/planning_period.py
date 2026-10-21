"""PlanningPeriod concept: the inclusive date window a plan is made for.

Rules (documented, deliberate):
- ``start_date <= end_date``; both endpoints are part of the period.
- ``working_days`` is derived, never stored: today it means Monday-Friday
  (weekends excluded, holidays not yet considered) and is computed by the
  isolated boundary in ``working_days.py`` — swap that module out for an
  MSP calendar later without touching anything else.
- The period says nothing about candidates, scoring, or ranking; it is a
  neutral window consumed by candidate discovery (date overlap) and,
  later, by the priority pipeline (where the working-day count matters).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from project_decision.domain.working_days import calculate_working_days


@dataclass(frozen=True)
class PlanningPeriod:
    """One inclusive planning window.

    ``working_days`` is a property, so it can never disagree with the
    dates and automatically follows the isolated calendar rule.
    """

    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        if self.start_date > self.end_date:
            raise ValueError("PlanningPeriod.start_date must not be after end_date")

    @property
    def working_days(self) -> int:
        """Working days (Mon-Fri today) inside the inclusive window."""
        return calculate_working_days(self.start_date, self.end_date)
