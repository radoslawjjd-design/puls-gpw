"""Date fixtures for the e2e suite, kept clock-independent.

Its own module rather than a helper inside ``conftest.py`` so it can be unit
tested without importing Playwright or standing up the fake BigQuery stores.
"""

import calendar
from datetime import date


def first_weekdays_of_month(n: int, today: date | None = None) -> list[date]:
    """Return the first ``n`` weekday dates of ``today``'s month, oldest first.

    Deliberately NOT bounded by "not in the future". The previous version was,
    and so returned fewer than ``n`` dates near the start of a month — on the 1st
    at most one weekday has elapsed, and none at all when the month opens on a
    Saturday. Callers index [0], [1] and [2] at import time, so the shortfall
    surfaced as an ``IndexError`` while collecting the module, taking the whole
    e2e suite down with it for the first days of every month.

    The dates must stay inside the current month: the CSV-export test asserts the
    download is named for the current month and that the fixture amounts appear
    in it, so walking backwards across the month boundary would break it exactly
    when this function matters most.
    """
    today = today or date.today()
    _, last = calendar.monthrange(today.year, today.month)
    days = [date(today.year, today.month, d) for d in range(1, last + 1)]
    weekdays = [d for d in days if d.weekday() < 5]
    if len(weekdays) < n:  # pragma: no cover - no Gregorian month has < 20
        raise ValueError(f"{today:%Y-%m} has only {len(weekdays)} weekdays, need {n}")
    return weekdays[:n]
