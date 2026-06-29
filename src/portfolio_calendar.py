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
    daily_change_pln (float), prices_found (int), total_positions (int).

    State values per day:
      'weekend'    — Saturday or Sunday
      'no_session' — Mon–Fri weekday absent from rows (GPW holiday or scraper gap)
      'data'       — trading day with prices_found > 0; pnl_abs = sum(shares * zmiana_kwotowa)
      'no_data'    — trading day in rows but prices_found == 0; pnl_abs is None
      'future'     — date is strictly after today (UTC)

    pnl_abs uses daily_change_pln (sum of shares × zmiana_kwotowa per position) — the same
    daily change metric shown in the Tabela view.  No lookback baseline needed.
    """
    today = datetime.now(tz=timezone.utc).date()
    _, last_day = calendar.monthrange(year, month)

    rows_by_date: dict[date, dict] = {r["snapshot_date"]: r for r in rows}

    days: list[dict] = []

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
            days.append({
                "date": iso, "day": day_num, "weekday": wd,
                "state": "no_session",
                "portfolio_value": None, "pnl_abs": None,
                "prices_found": 0, "total_positions": 0,
            })
            continue

        row = rows_by_date[d]
        pf_found = row["prices_found"]
        total_pos = row["total_positions"]

        if pf_found > 0:
            state = "data"
            pnl: float | None = row.get("daily_change_pln")
        else:
            state = "no_data"
            pnl = None

        days.append({
            "date": iso, "day": day_num, "weekday": wd,
            "state": state,
            "portfolio_value": row["portfolio_value"],
            "pnl_abs": pnl,
            "prices_found": pf_found,
            "total_positions": total_pos,
        })

    return {"year": year, "month": month, "days": days}
