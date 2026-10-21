"""Tests for PlanningPeriod and the isolated working-day calculation."""

from datetime import date

import pytest

from project_decision.domain import PlanningPeriod, calculate_working_days


def period(start, end) -> PlanningPeriod:
    return PlanningPeriod(start_date=start, end_date=end)


class TestWorkingDaysCalculation:
    def test_one_monday_to_friday_week_is_five_working_days(self):
        week = calculate_working_days(date(2026, 6, 1), date(2026, 6, 5))
        assert week == 5  # Mon 1 - Fri 5 June 2026

    def test_two_working_weeks_are_ten_working_days(self):
        # Mon 1 June .. Fri 12 June = two Mon-Fri weeks.
        assert calculate_working_days(date(2026, 6, 1), date(2026, 6, 12)) == 10

    def test_weekend_only_period_is_zero(self):
        # Sat 6 June .. Sun 7 June 2026.
        assert calculate_working_days(date(2026, 6, 6), date(2026, 6, 7)) == 0

    def test_single_fridays_and_mondays_count_as_one(self):
        assert calculate_working_days(date(2026, 6, 5), date(2026, 6, 5)) == 1
        assert calculate_working_days(date(2026, 6, 1), date(2026, 6, 1)) == 1

    def test_boundaries_are_inclusive(self):
        # Fri 5 .. Mon 8 June: Fri + Mon are working days, weekend is not.
        assert calculate_working_days(date(2026, 6, 5), date(2026, 6, 8)) == 2

    def test_weekends_inside_longer_periods_do_not_count(self):
        # Sat 30 May .. Sun 14 June: two Mon-Fri weeks (Jun 1-5, Jun 8-12).
        assert calculate_working_days(date(2026, 5, 30), date(2026, 6, 14)) == 10

    def test_spans_across_month_and_year_boundaries(self):
        # Wed 31 Dec 2025 .. Fri 2 Jan 2026: Wed, Thu, Fri = 3 days.
        assert calculate_working_days(date(2025, 12, 31), date(2026, 1, 2)) == 3

    def test_invalid_range_is_rejected(self):
        with pytest.raises(ValueError, match="after end_date"):
            calculate_working_days(date(2026, 6, 5), date(2026, 6, 1))


class TestPlanningPeriod:
    def test_valid_period_keeps_dates_and_computes_working_days(self):
        p = period(date(2026, 6, 1), date(2026, 6, 5))

        assert p.start_date == date(2026, 6, 1)
        assert p.end_date == date(2026, 6, 5)
        assert p.working_days == 5

    def test_working_days_follow_the_dates_automatically(self):
        assert period(date(2026, 6, 6), date(2026, 6, 7)).working_days == 0
        assert period(date(2026, 6, 1), date(2026, 6, 12)).working_days == 10

    def test_start_equals_end_is_valid(self):
        assert period(date(2026, 6, 1), date(2026, 6, 1)).working_days == 1

    def test_invalid_period_is_rejected(self):
        with pytest.raises(ValueError, match="after end_date"):
            period(date(2026, 6, 5), date(2026, 6, 1))
