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


def test_weekday_absent_from_rows_is_no_session():
    """Mon–Fri absent from BQ rows → state='no_session' (GPW holiday or scraper gap)."""
    rows = [
        # June 2 is Tuesday — skip it (no row)
        _make_row(date(2026, 6, 3), 10000.0),  # Wednesday
    ]
    result = compute_calendar_pnl(rows, 2026, 6)
    days = {d["date"]: d for d in result["days"]}
    assert days["2026-06-02"]["state"] == "no_session"


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
    # Use a past month to avoid flakiness — July 2030 is entirely future relative to 2026-06-29
    result = compute_calendar_pnl([], 2030, 7)
    non_weekend = [d for d in result["days"] if d["weekday"] < 5]
    assert all(d["state"] == "future" for d in non_weekend)


def test_empty_rows_all_non_weekends_are_no_session_or_future():
    """With empty rows, weekdays are no_session or future; weekends stay weekend."""
    result = compute_calendar_pnl([], 2026, 6)
    for d in result["days"]:
        if d["weekday"] >= 5:
            assert d["state"] == "weekend"
        else:
            assert d["state"] in ("no_session", "future")


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
