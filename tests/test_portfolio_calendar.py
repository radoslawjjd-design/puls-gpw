"""Unit tests for src/portfolio_calendar.py — compute_calendar_pnl()."""
from datetime import date

import pytest

from src.portfolio_calendar import compute_calendar_pnl


def _make_row(
    snapshot_date: date,
    portfolio_value: float,
    daily_change_pln: float = 0.0,
    prices_found: int = 1,
    total_positions: int = 1,
) -> dict:
    return {
        "snapshot_date": snapshot_date,
        "portfolio_value": portfolio_value,
        "daily_change_pln": daily_change_pln,
        "prices_found": prices_found,
        "total_positions": total_positions,
    }


# ── basic shape ──────────────────────────────────────────────────────────────

def test_returns_correct_month_year_and_day_count():
    """Result has year, month, and exactly 30 days for June 2026."""
    result = compute_calendar_pnl([], 2026, 6)
    assert result["year"] == 2026
    assert result["month"] == 6
    assert len(result["days"]) == 30


def test_february_non_leap_year_has_28_days():
    result = compute_calendar_pnl([], 2025, 2)
    assert len(result["days"]) == 28


def test_february_leap_year_has_29_days():
    result = compute_calendar_pnl([], 2024, 2)
    assert len(result["days"]) == 29


# ── state classification ─────────────────────────────────────────────────────

def test_weekends_are_classified_as_weekend():
    """Saturday and Sunday get state='weekend'."""
    result = compute_calendar_pnl([], 2026, 6)
    days = {d["date"]: d for d in result["days"]}
    # June 6, 2026 = Saturday; June 7, 2026 = Sunday
    assert days["2026-06-06"]["state"] == "weekend"
    assert days["2026-06-07"]["state"] == "weekend"


def test_weekday_absent_from_rows_is_no_data():
    """Mon–Fri absent from BQ rows and not a GPW holiday → state='no_data' (white in UI)."""
    rows = [
        # June 2 is Tuesday — skip it (no row)
        _make_row(date(2026, 6, 3), 10000.0),  # Wednesday
    ]
    result = compute_calendar_pnl(rows, 2026, 6)
    days = {d["date"]: d for d in result["days"]}
    assert days["2026-06-02"]["state"] == "no_data"


def test_gpw_holiday_gets_holiday_state():
    """Official GPW holidays (from _GPW_HOLIDAYS) → state='holiday' (gray in UI)."""
    result = compute_calendar_pnl([], 2026, 6)
    days = {d["date"]: d for d in result["days"]}
    # June 4, 2026 = Boże Ciało (Corpus Christi) — official GPW holiday
    assert days["2026-06-04"]["state"] == "holiday"


def test_trading_day_with_prices_has_state_data():
    """A weekday present in rows with prices_found > 0 → state='data'."""
    rows = [
        _make_row(date(2026, 6, 2), 10000.0, prices_found=2, total_positions=2),
        _make_row(date(2026, 6, 3), 10200.0, prices_found=2, total_positions=2),
    ]
    result = compute_calendar_pnl(rows, 2026, 6)
    days = {d["date"]: d for d in result["days"]}
    assert days["2026-06-03"]["state"] == "data"


def test_future_days_get_future_state():
    """Days after today → state='future'."""
    result = compute_calendar_pnl([], 2030, 7)
    non_weekend = [d for d in result["days"] if d["weekday"] < 5]
    # non-holidays in future month should be future (July 2030 has no _GPW_HOLIDAYS entries)
    assert all(d["state"] == "future" for d in non_weekend)


def test_empty_rows_weekdays_are_no_data_holiday_or_future():
    """With empty rows, weekdays are no_data / holiday / future; weekends stay weekend."""
    result = compute_calendar_pnl([], 2026, 6)
    for d in result["days"]:
        if d["weekday"] >= 5:
            assert d["state"] == "weekend"
        else:
            assert d["state"] in ("no_data", "holiday", "future")


# ── P&L delta computation ────────────────────────────────────────────────────

def test_pnl_abs_comes_from_daily_change_pln():
    """pnl_abs = daily_change_pln (shares × zmiana_kwotowa) — same as Tabela view."""
    rows = [
        _make_row(date(2026, 6, 2), 10000.0, daily_change_pln=300.0),
        _make_row(date(2026, 6, 3), 10300.0, daily_change_pln=-50.0),
    ]
    result = compute_calendar_pnl(rows, 2026, 6)
    days = {d["date"]: d for d in result["days"]}
    assert days["2026-06-02"]["pnl_abs"] == pytest.approx(300.0)
    assert days["2026-06-03"]["pnl_abs"] == pytest.approx(-50.0)


def test_pnl_abs_is_negative_on_loss_day():
    rows = [_make_row(date(2026, 6, 2), 9750.0, daily_change_pln=-250.0)]
    result = compute_calendar_pnl(rows, 2026, 6)
    days = {d["date"]: d for d in result["days"]}
    assert days["2026-06-02"]["pnl_abs"] == pytest.approx(-250.0)


def test_lookback_rows_ignored_pnl_uses_daily_change_directly():
    """Lookback rows (before month_start) don't affect P&L — daily_change_pln is used directly."""
    rows = [
        _make_row(date(2026, 5, 29), 9800.0, daily_change_pln=50.0),  # lookback — ignored
        _make_row(date(2026, 6, 2), 10000.0, daily_change_pln=200.0),
    ]
    result = compute_calendar_pnl(rows, 2026, 6)
    days = {d["date"]: d for d in result["days"]}
    assert days["2026-06-02"]["pnl_abs"] == pytest.approx(200.0)


def test_zero_daily_change_shows_zero_pnl():
    rows = [_make_row(date(2026, 6, 2), 10000.0, daily_change_pln=0.0)]
    result = compute_calendar_pnl(rows, 2026, 6)
    days = {d["date"]: d for d in result["days"]}
    assert days["2026-06-02"]["pnl_abs"] == pytest.approx(0.0)


# ── best-effort (partial prices) ─────────────────────────────────────────────

def test_partial_prices_still_produce_data_state():
    """prices_found < total_positions is allowed — state is still 'data', pnl from daily_change_pln."""
    rows = [
        _make_row(date(2026, 6, 2), 5000.0, daily_change_pln=80.0, prices_found=1, total_positions=3),
        _make_row(date(2026, 6, 3), 5200.0, daily_change_pln=200.0, prices_found=1, total_positions=3),
    ]
    result = compute_calendar_pnl(rows, 2026, 6)
    days = {d["date"]: d for d in result["days"]}
    assert days["2026-06-03"]["state"] == "data"
    assert days["2026-06-03"]["pnl_abs"] == pytest.approx(200.0)


# ── metadata fields ──────────────────────────────────────────────────────────

def test_day_object_has_all_required_fields():
    """Each day dict has: date, day, weekday, state, portfolio_value, pnl_abs, prices_found, total_positions."""
    result = compute_calendar_pnl([], 2026, 6)
    required = {"date", "day", "weekday", "state", "portfolio_value", "pnl_abs", "prices_found", "total_positions", "mtd_diff"}
    for d in result["days"]:
        assert required.issubset(d.keys()), f"Missing keys in {d}"


def test_weekday_field_is_0_for_monday():
    """weekday=0 for Monday (per Python's date.weekday() convention)."""
    # June 1, 2026 = Monday
    result = compute_calendar_pnl([], 2026, 6)
    days = {d["date"]: d for d in result["days"]}
    assert days["2026-06-01"]["weekday"] == 0


def test_date_field_is_iso_string():
    result = compute_calendar_pnl([], 2026, 6)
    assert result["days"][0]["date"] == "2026-06-01"


# ── MTD diff ─────────────────────────────────────────────────────────────────

def test_mtd_diff_is_cumulative_daily_pnl():
    """mtd_diff = running sum of daily_change_pln across data days in chronological order."""
    rows = [
        _make_row(date(2026, 6, 2), 10012.0, daily_change_pln=12.0),
        _make_row(date(2026, 6, 3), 9961.0, daily_change_pln=-51.0),
        _make_row(date(2026, 6, 5), 10398.0, daily_change_pln=437.0),
    ]
    result = compute_calendar_pnl(rows, 2026, 6)
    days = {d["date"]: d for d in result["days"]}
    assert days["2026-06-02"]["mtd_diff"] == pytest.approx(12.0)
    assert days["2026-06-03"]["mtd_diff"] == pytest.approx(-39.0)
    assert days["2026-06-05"]["mtd_diff"] == pytest.approx(398.0)


def test_mtd_diff_includes_first_day_pnl():
    """First data day's pnl is included in MTD, not zeroed as with the old baseline approach."""
    rows = [
        _make_row(date(2026, 6, 1), 10500.0, daily_change_pln=500.0),
        _make_row(date(2026, 6, 2), 10800.0, daily_change_pln=300.0),
    ]
    result = compute_calendar_pnl(rows, 2026, 6)
    days = {d["date"]: d for d in result["days"]}
    assert days["2026-06-01"]["mtd_diff"] == pytest.approx(500.0)
    assert days["2026-06-02"]["mtd_diff"] == pytest.approx(800.0)


def test_mtd_diff_lookback_rows_do_not_affect_cumulative():
    """Rows from previous month are never processed in the loop — cumulative starts at 0."""
    rows = [
        _make_row(date(2026, 5, 29), 9500.0, daily_change_pln=500.0),  # previous month
        _make_row(date(2026, 6, 2), 9800.0, daily_change_pln=300.0),
    ]
    result = compute_calendar_pnl(rows, 2026, 6)
    days = {d["date"]: d for d in result["days"]}
    assert days["2026-06-02"]["mtd_diff"] == pytest.approx(300.0)


def test_mtd_diff_none_when_no_rows_at_all():
    """Completely empty rows → mtd_diff = None."""
    result = compute_calendar_pnl([], 2026, 6)
    for d in result["days"]:
        assert d["mtd_diff"] is None


def test_mtd_diff_none_for_non_data_states():
    """weekend, holiday, no_data, partial, future → mtd_diff = None."""
    rows = [
        _make_row(date(2026, 6, 1), 10000.0),  # baseline
        # June 4 = holiday (Corpus Christi), June 6-7 = weekend
        _make_row(date(2026, 6, 5), 10100.0, prices_found=0),  # partial
    ]
    result = compute_calendar_pnl(rows, 2026, 6)
    days = {d["date"]: d for d in result["days"]}
    assert days["2026-06-04"]["state"] == "holiday"
    assert days["2026-06-04"]["mtd_diff"] is None
    assert days["2026-06-06"]["state"] == "weekend"
    assert days["2026-06-06"]["mtd_diff"] is None
    assert days["2026-06-05"]["state"] == "partial"
    assert days["2026-06-05"]["mtd_diff"] is None


# ── pre-inception months (PUL-103) ───────────────────────────────────────────

def test_month_entirely_before_inception_renders_a_full_blank_grid():
    """PUL-103.  A month before the portfolio's first trade returns no rows at all,
    and the grid must still be complete and blank — the same as a month that has not
    arrived yet.

    This is the contract the whole "no row instead of a new state" decision rests on.
    Emitting a zero instead would render as a real flat session: the JS paints any
    `state == 'data'` day with `pnl_abs >= 0` green (static/index.html:4900), so a
    zero shows up as a green "+0 PLN" cell. And an empty `days` array would throw in
    the renderer, which reads `data.days[0].weekday` unguarded (:4873).
    """
    result = compute_calendar_pnl([], 2024, 6)

    assert len(result["days"]) == 30, "the grid must stay complete even with no data"
    for day in result["days"]:
        assert day["state"] in {"weekend", "holiday", "no_data"}
        assert day["pnl_abs"] is None, "a pre-inception day must carry no number"
        assert day["portfolio_value"] is None
        assert day["mtd_diff"] is None
    assert any(d["state"] == "no_data" for d in result["days"]), (
        "weekdays before inception are no_data, which the UI leaves blank"
    )


def test_days_before_inception_carry_the_same_blank_payload_as_future_days():
    """Both render blank: the renderer has a branch for 'data', 'weekend' and
    'holiday' and for nothing else, so 'no_data' and 'future' are painted the same
    empty cell.  What has to match is the payload, not the calendar layout."""
    def payloads(result):
        return {(d["pnl_abs"], d["portfolio_value"], d["mtd_diff"])
                for d in result["days"] if d["state"] not in {"weekend", "holiday"}}

    past = compute_calendar_pnl([], 2024, 6)
    future = compute_calendar_pnl([], 2099, 6)

    assert {d["state"] for d in past["days"]} <= {"weekend", "holiday", "no_data"}
    assert {d["state"] for d in future["days"]} <= {"weekend", "holiday", "future"}
    assert payloads(past) == payloads(future) == {(None, None, None)}


# ── PUL-111: why the exchange was closed ─────────────────────────────────────

def test_a_statutory_holiday_says_which_one_it_is():
    """A weekday with no bar said nothing about why. Naming the holiday is the
    difference between "the country is off" and "something is broken"."""
    result = compute_calendar_pnl([], 2026, 5)
    days = {d["date"]: d for d in result["days"]}

    assert days["2026-05-01"]["state"] == "holiday"
    assert days["2026-05-01"]["reason"] == "Święto Pracy — dzień ustawowo wolny od pracy"


def test_an_easter_dependent_holiday_is_computed_not_tabulated():
    """Corpus Christi moves with Easter, so a fixed table would be wrong every year.
    4 June 2026 is Easter (5 April) + 60 days."""
    result = compute_calendar_pnl([], 2026, 6)
    days = {d["date"]: d for d in result["days"]}

    assert days["2026-06-04"]["state"] == "holiday"
    assert days["2026-06-04"]["reason"].startswith("Boże Ciało")


def test_an_exchange_only_closure_does_not_claim_to_be_a_public_holiday():
    """31 December is a working day in Poland — the exchange simply does not trade.
    Saying "dzień ustawowo wolny" there would be a plain falsehood."""
    result = compute_calendar_pnl([], 2026, 12)
    days = {d["date"]: d for d in result["days"]}

    assert days["2026-12-31"]["state"] == "holiday"
    assert days["2026-12-31"]["reason"] == "Dzień wolny od sesji — decyzja GPW"


def test_christmas_eve_is_statutory_only_from_the_year_it_became_one():
    """Christmas Eve became a non-working day in Poland in 2025; before that GPW
    closed on it by its own decision. The reason has to follow the law, not the
    habit — 2024 is checked through the helper because GPW's own list does not
    reach back that far."""
    from src.portfolio_calendar import _statutory_holiday_name

    assert _statutory_holiday_name(date(2026, 12, 24)) == "Wigilia Bożego Narodzenia"
    assert _statutory_holiday_name(date(2024, 12, 24)) is None


def test_a_day_that_traded_gives_no_closure_reason():
    """`reason` answers "why was it shut". A session day was not shut, and a filled
    string there would put a contradiction next to a P&L number."""
    rows = [_make_row(date(2026, 6, 3), 10000.0, 150.0)]
    result = compute_calendar_pnl(rows, 2026, 6)
    days = {d["date"]: d for d in result["days"]}

    assert days["2026-06-03"]["state"] == "data"
    assert days["2026-06-03"]["reason"] is None
    assert days["2026-06-06"]["reason"] == "Weekend — giełda nie prowadzi sesji"


# ── injected clock (PUL-121) ─────────────────────────────────────────────────

def test_today_can_be_pinned_so_the_future_boundary_is_not_the_wall_clock():
    """The e2e fixture needs days that are inside the rendered month AND already
    past. On the first of a month no such day exists, so the boundary has to be
    injectable — otherwise the suite is uncollectable for the first days of every
    month, which is exactly what happened."""
    rows = [_make_row(date(2026, 6, 3), 10000.0, 150.0)]

    result = compute_calendar_pnl(rows, 2026, 6, today=date(2026, 6, 4))
    days = {d["date"]: d for d in result["days"]}

    assert days["2026-06-03"]["state"] == "data"
    assert days["2026-06-04"]["state"] != "future"
    assert days["2026-06-05"]["state"] == "future"


def test_omitting_today_still_uses_the_real_clock():
    """The parameter is a seam for tests, not a behaviour change: a month long
    past has no future days whatever the wall clock says."""
    result = compute_calendar_pnl([], 2020, 6)

    assert not any(d["state"] == "future" for d in result["days"])
