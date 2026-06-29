"""Pure compute function for the P&L calendar view (PUL-59).

No I/O — takes raw BQ rows and builds a full monthly grid with state and P&L.
"""
import calendar
from datetime import date, datetime, timezone

# GPW official no-session days (holidays) — source: https://www.gpw.pl/szczegoly-sesji
_GPW_HOLIDAYS: frozenset[date] = frozenset([
    # 2025
    date(2025, 1, 1), date(2025, 1, 6), date(2025, 4, 18), date(2025, 4, 21),
    date(2025, 5, 1), date(2025, 6, 19), date(2025, 8, 15), date(2025, 11, 11),
    date(2025, 12, 24), date(2025, 12, 25), date(2025, 12, 26), date(2025, 12, 31),
    # 2026
    date(2026, 1, 1), date(2026, 1, 6), date(2026, 4, 3), date(2026, 4, 6),
    date(2026, 5, 1), date(2026, 6, 4), date(2026, 11, 11),
    date(2026, 12, 24), date(2026, 12, 25), date(2026, 12, 31),
    # 2027
    date(2027, 1, 1), date(2027, 1, 6), date(2027, 3, 26), date(2027, 3, 29),
    date(2027, 5, 3), date(2027, 5, 27), date(2027, 11, 1), date(2027, 11, 11),
    date(2027, 12, 24), date(2027, 12, 31),
])


def compute_calendar_pnl(
    rows: list[dict],
    year: int,
    month: int,
) -> dict:
    """Build a full monthly calendar from BQ rows returned by get_portfolio_calendar_data().

    Each entry in rows must have: snapshot_date (date), portfolio_value (float),
    daily_change_pln (float), prices_found (int), total_positions (int).

    State values per day:
      'weekend'    — Saturday or Sunday (always gray in UI)
      'holiday'    — official GPW no-session day from _GPW_HOLIDAYS (gray in UI)
      'no_data'    — weekday with no BQ row (portfolio missing data / no history) — white
      'data'       — trading day with prices_found > 0; pnl_abs = sum(shares * zmiana_kwotowa)
      'partial'    — trading day in rows but prices_found == 0; pnl_abs is None — white
      'future'     — date is strictly after today (UTC) — white

    Gray in UI: weekend + holiday only.
    White in UI: everything else without a green/red value.
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

        if d in _GPW_HOLIDAYS:
            days.append({
                "date": iso, "day": day_num, "weekday": wd,
                "state": "holiday",
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
                "state": "no_data",
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
            state = "partial"
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
