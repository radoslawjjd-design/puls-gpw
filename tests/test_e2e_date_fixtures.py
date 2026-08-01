"""The e2e suite's date fixtures must not depend on what day it is run.

PUL-121: `tests/e2e/conftest.py` indexed [0], [1] and [2] into a list built at
import time from "weekdays of this month so far". For the first days of any month
that list was shorter, so collecting the module raised IndexError and the entire
e2e directory failed to collect — `uv run pytest` exited non-zero with the unit
suite perfectly green. Lives outside tests/e2e/ on purpose: it must run even when
Playwright browsers are not installed.
"""

from datetime import date

import pytest

from tests.e2e._dates import first_weekdays_of_month


@pytest.mark.parametrize(
    "today, expected",
    [
        # The 1st on a Saturday — zero weekdays elapsed. This is the case that
        # broke CI on 2026-08-01 and again on the 2nd.
        (date(2026, 8, 1), [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)]),
        (date(2026, 8, 2), [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)]),
        # The 1st on a weekday — one weekday elapsed, so [1] and [2] used to fail.
        (date(2026, 9, 1), [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]),
        # Mid-month — the case that always worked, and must keep working.
        (date(2026, 9, 20), [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]),
        # A month opening on a Sunday.
        (date(2026, 11, 1), [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)]),
    ],
)
def test_three_dates_come_back_whatever_the_day(today, expected):
    assert first_weekdays_of_month(3, today=today) == expected


def test_every_date_is_a_weekday_and_inside_the_month():
    # The dates must stay in the current month: the CSV-export test asserts the
    # download is named for it and that these amounts appear inside.
    for month in range(1, 13):
        got = first_weekdays_of_month(3, today=date(2026, month, 1))
        # Ascending by date — NOT by weekday number, which wraps whenever the
        # three days straddle a weekend (Fri, Mon, Tue -> 4, 0, 1).
        assert got == sorted(got)
        assert all(d.weekday() < 5 for d in got)
        assert all(d.month == month and d.year == 2026 for d in got)


def test_the_result_does_not_move_within_a_month():
    first = first_weekdays_of_month(3, today=date(2026, 8, 1))
    last = first_weekdays_of_month(3, today=date(2026, 8, 31))

    assert first == last
