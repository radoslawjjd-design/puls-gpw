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
    required = {"date", "day", "weekday", "state", "portfolio_value", "pnl_abs", "prices_found", "total_positions"}
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

def test_mtd_diff_zero_when_day1_is_trading_day():
    """Day 1 with data: mtd_diff = portfolio_value[day_1] - baseline = 0."""
    rows = [
        _make_row(date(2026, 6, 1), 10000.0),  # Monday, trading day → baseline
        _make_row(date(2026, 6, 2), 10300.0),
    ]
    result = compute_calendar_pnl(rows, 2026, 6)
    days = {d["date"]: d for d in result["days"]}
    assert days["2026-06-01"]["mtd_diff"] == pytest.approx(0.0)
    assert days["2026-06-02"]["mtd_diff"] == pytest.approx(300.0)


def test_mtd_diff_accumulates_across_month():
    """MTD grows with each data day relative to baseline."""
    rows = [
        _make_row(date(2026, 6, 1), 10000.0),  # baseline
        _make_row(date(2026, 6, 2), 10200.0),
        _make_row(date(2026, 6, 3), 9800.0),   # loss day but MTD still -200 from baseline
    ]
    result = compute_calendar_pnl(rows, 2026, 6)
    days = {d["date"]: d for d in result["days"]}
    assert days["2026-06-01"]["mtd_diff"] == pytest.approx(0.0)
    assert days["2026-06-02"]["mtd_diff"] == pytest.approx(200.0)
    assert days["2026-06-03"]["mtd_diff"] == pytest.approx(-200.0)


def test_mtd_diff_uses_lookback_when_day1_is_weekend():
    """When day 1 = weekend/holiday, baseline = last trading day of previous month."""
    # July 2026: day 1 = Wednesday (trading day) — use a month starting on weekend instead
    # Use custom: pretend we're computing June 2026, but add a lookback row from May
    # and no row for June 1 (simulate no_data on June 1)
    rows = [
        _make_row(date(2026, 5, 29), 9500.0),  # lookback, last trading day before June 1
        _make_row(date(2026, 6, 2), 9800.0),   # first data day in June
    ]
    result = compute_calendar_pnl(rows, 2026, 6)
    days = {d["date"]: d for d in result["days"]}
    # baseline = 9500 (max snapshot_date <= 2026-06-01)
    assert days["2026-06-02"]["mtd_diff"] == pytest.approx(300.0)


def test_mtd_diff_none_when_no_baseline():
    """No rows at or before day 1 → mtd_diff = None for all data days."""
    rows = [
        _make_row(date(2026, 6, 3), 10000.0),  # no lookback row before or on June 1
    ]
    result = compute_calendar_pnl(rows, 2026, 6)
    days = {d["date"]: d for d in result["days"]}
    assert days["2026-06-03"]["mtd_diff"] is None


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
