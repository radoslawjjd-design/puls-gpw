import json


def compute_user_portfolio_treemap_positions(rows: list[dict]) -> list[dict]:
    """Compute treemap fields for user portfolio positions from list_user_portfolio_positions output.

    Input rows: ticker, company_name, shares, avg_buy_price, current_price (float|None),
    daily_change_pct (float|None).

    Positions with no price are included in output (position_value_pln=None) so the
    frontend can show a no-price notice; they are excluded from the portfolio_share_pct
    denominator.

    Pure function — no BQ/network access.
    """
    total_value = sum(
        row.get("shares", 0.0) * row["current_price"]
        for row in rows
        if row.get("current_price") is not None
    )

    result = []
    for row in rows:
        current_price: float | None = row.get("current_price")
        avg_buy_price: float = row.get("avg_buy_price") or 0.0
        shares: float = row.get("shares") or 0.0
        d_pct: float | None = row.get("daily_change_pct")

        if current_price is not None:
            position_value_pln: float | None = shares * current_price
        else:
            position_value_pln = None

        if position_value_pln is not None and d_pct is not None:
            _denom = 1 + d_pct / 100
            daily_change_pln: float | None = position_value_pln * d_pct / 100 / _denom if _denom != 0 else None
        else:
            daily_change_pln = None

        if current_price is not None and avg_buy_price != 0:
            since_purchase_pct: float | None = (current_price / avg_buy_price - 1) * 100
        else:
            since_purchase_pct = None

        if current_price is not None:
            since_purchase_pln: float | None = (current_price - avg_buy_price) * shares
        else:
            since_purchase_pln = None

        if position_value_pln is not None and total_value != 0:
            portfolio_share_pct: float | None = position_value_pln / total_value * 100
        else:
            portfolio_share_pct = None

        result.append({
            "ticker": row.get("ticker"),
            "company_name": row.get("company_name"),
            "position_value_pln": position_value_pln,
            "daily_change_pct": d_pct,
            "daily_change_pln": daily_change_pln,
            "since_purchase_pct": since_purchase_pct,
            "since_purchase_pln": since_purchase_pln,
            "portfolio_share_pct": portfolio_share_pct,
        })
    return result


def compute_treemap_positions(
    today_positions_json: str, yesterday_positions_json: str | None, total_value: float
) -> list[dict]:
    """Compute each of today's positions' value + daily delta vs. yesterday, matched by ticker.

    Also computes each position's % share of the wallet's total portfolio value
    (`total_value` — positions + cash, not sum-of-positions), or None if
    `total_value == 0` (guards division by zero).

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
        try:
            portfolio_share_pct = (value / total_value * 100) if total_value != 0 else None
        except TypeError:
            portfolio_share_pct = None
        pct = position.get("pct")
        if not isinstance(pct, (int, float)):
            since_purchase_pct: float | None = None
            since_purchase_pln: float | None = None
        else:
            denom = 1 + pct / 100
            if denom == 0:
                since_purchase_pct = None
                since_purchase_pln = None
            else:
                cost = value / denom
                since_purchase_pct = pct
                since_purchase_pln = value - cost
        result.append({
            "ticker": ticker,
            "position_value_pln": value,
            "daily_change_pln": daily_change_pln,
            "daily_change_pct": daily_change_pct,
            "portfolio_share_pct": portfolio_share_pct,
            "since_purchase_pct": since_purchase_pct,
            "since_purchase_pln": since_purchase_pln,
        })
    return result
