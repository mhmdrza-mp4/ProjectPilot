"""Working-day counting: the isolated calendar boundary.

Currently the rule is deliberately simple: working days are Monday to
Friday, endpoints inclusive; weekends are excluded; holidays and
Microsoft Project calendars are NOT considered yet.

This function is the only place that knows what a "working day" is.
A future MSP calendar or a custom calendar replaces this module (and
optionally wraps ``PlanningPeriod.working_days``) WITHOUT changing any
candidate-selection or later priority logic, which only consume the
computed number — never the weekday rule.
"""

from __future__ import annotations

from datetime import date


def calculate_working_days(start_date: date, end_date: date) -> int:
    """Count working days (Mon-Fri) from ``start_date`` to ``end_date`` inclusive.

    Raises:
        ValueError: ``start_date`` is after ``end_date``.
    """
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")

    total_days = (end_date - start_date).days + 1
    full_weeks, remaining = divmod(total_days, 7)
    weekdays = full_weeks * 5
    start_weekday = start_date.weekday()  # Monday == 0, Sunday == 6
    for offset in range(remaining):
        if (start_weekday + offset) % 7 < 5:  # 0..4 == Mon..Fri
            weekdays += 1
    return weekdays
