import json


def compute_treemap_positions(
    today_positions_json: str, yesterday_positions_json: str | None
) -> list[dict]:
    """Compute each of today's positions' value + daily delta vs. yesterday, matched by ticker.

    Pure function — no BQ/network access. Returns [] on malformed/unparseable JSON
    rather than raising, so the endpoint never crashes on bad data.
    """
    try:
        today_positions = json.loads(today_positions_json)["positions"]
    except Exception:
        return []

    yesterday_by_ticker: dict[str, float] = {}
    if yesterday_positions_json is not None:
        try:
            yesterday_positions = json.loads(yesterday_positions_json)["positions"]
        except Exception:
            yesterday_positions = []
        for position in yesterday_positions:
            try:
                yesterday_by_ticker[position["ticker"]] = position["value"]
            except (KeyError, TypeError):
                continue

    result = []
    for position in today_positions:
        try:
            ticker = position["ticker"]
            value = position["value"]
        except (KeyError, TypeError):
            continue
        yesterday_value = yesterday_by_ticker.get(ticker)
        if yesterday_value is None:
            daily_change_pln: float | None = None
            daily_change_pct: float | None = None
        else:
            daily_change_pln = value - yesterday_value
            daily_change_pct = (daily_change_pln / yesterday_value * 100) if yesterday_value != 0 else None
        result.append({
            "ticker": ticker,
            "position_value_pln": value,
            "daily_change_pln": daily_change_pln,
            "daily_change_pct": daily_change_pct,
        })
    return result
