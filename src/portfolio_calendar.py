"""Pure compute function for the P&L calendar view (PUL-59).

No I/O — takes raw BQ rows and builds a full monthly grid with state and P&L.
"""
import calendar
from datetime import date, datetime, timezone


def compute_calendar_pnl(
    rows: list[dict],
    year: int,
    month: int,
) -> dict:
    """Build a full monthly calendar from BQ rows returned by get_portfolio_calendar_data().

    Each entry in rows must have: snapshot_date (date), portfolio_value (float),
    prices_found (int), total_positions (int).

    State values per day:
      'weekend'    — Saturday or Sunday
      'no_session' — Mon–Fri weekday absent from rows (GPW holiday or scraper gap)
      'data'       — trading day with prices_found > 0; pnl_abs is set
      'no_data'    — trading day in rows but prices_found == 0; pnl_abs is None
      'future'     — date is strictly after today (UTC)

    pnl_abs = portfolio_value[D] − portfolio_value[D−1] using consecutive trading-day
    entries. The lookback baseline (entry before month_start) drives the first trading
    day's P&L. If no baseline exists, pnl_abs for the first trading day is None.
    """
    today = datetime.now(tz=timezone.utc).date()
    month_start = date(year, month, 1)
    _, last_day = calendar.monthrange(year, month)

    # Sort rows by date and split into lookback (< month_start) and in-month buckets
    sorted_rows = sorted(rows, key=lambda r: r["snapshot_date"])
    rows_by_date: dict[date, dict] = {r["snapshot_date"]: r for r in sorted_rows}

    # Find the lookback baseline: last entry strictly before month_start
    lookback_rows = [r for r in sorted_rows if r["snapshot_date"] < month_start]
    prev_value: float | None = lookback_rows[-1]["portfolio_value"] if lookback_rows else None

    days: list[dict] = []
    prev_trading_value = prev_value

    for day_num in range(1, last_day + 1):
        d = date(year, month, day_num)
        iso = d.isoformat()
        wd = d.weekday()  # 0=Mon … 6=Sun

        if wd >= 5:
            days.append({
                "date": iso, "day": day_num, "weekday": wd,
                "state": "weekend",
                "portfolio_value": None, "pnl_abs": None,
                "prices_found": 0, "total_positions": 0,
            })
            continue

        if d > today:
            days.append({
                "date": iso, "day": day_num, "weekday": wd,
                "state": "future",
                "portfolio_value": None, "pnl_abs": None,
                "prices_found": 0, "total_positions": 0,
            })
            continue

        if d not in rows_by_date:
            # Weekday not present in BQ → non-trading day (GPW holiday or scraper gap)
            days.append({
                "date": iso, "day": day_num, "weekday": wd,
                "state": "no_session",
                "portfolio_value": None, "pnl_abs": None,
                "prices_found": 0, "total_positions": 0,
            })
            continue

        row = rows_by_date[d]
        pf = row["portfolio_value"]
        pf_found = row["prices_found"]
        total_pos = row["total_positions"]

        pnl: float | None
        if pf_found > 0:
            state = "data"
            pnl = (pf - prev_trading_value) if prev_trading_value is not None else None
        else:
            state = "no_data"
            pnl = None

        prev_trading_value = pf

        days.append({
            "date": iso, "day": day_num, "weekday": wd,
            "state": state,
            "portfolio_value": pf,
            "pnl_abs": pnl,
            "prices_found": pf_found,
            "total_positions": total_pos,
        })

    return {"year": year, "month": month, "days": days}
