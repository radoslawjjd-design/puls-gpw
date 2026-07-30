"""Realized profit and loss from a broker's trade history.

Pure logic — no BigQuery, no FastAPI. Takes plain dicts so the same function
serves a freshly parsed export and rows read back out of user_broker_operations.

Deliberately separate from the dividend totals. A dividend is cash the company
paid; a realized result is what a sale actually returned against what the shares
cost. Adding them into one number would hide which of the two a portfolio is
actually living on.
"""

OP_BUY = "buy"
OP_SELL = "sell"

# Fractional shares are everywhere in these exports, so a lot that is fully
# consumed can land a hair above zero rather than exactly on it.
_DUST = 1e-9


def _sort_key(op: dict):
    """Chronological, with a stable tiebreak.

    Rows are not guaranteed to arrive in order and FIFO is only meaningful
    against real time order. Same-timestamp rows are broken by buy-before-sell:
    a fill and its closing fill occasionally share a timestamp to the
    microsecond, and consuming a lot that has not been recorded yet would drop
    the cost basis on the floor.
    """
    return (op.get("occurred_at"), 0 if op.get("op_type") == OP_BUY else 1)


def compute_realized_pnl(operations: list[dict], year: int | None = None) -> dict:
    """Match sells against buys FIFO and total what each ticker actually returned.

    Input rows need ``ticker``, ``op_type``, ``occurred_at``, ``volume`` and
    ``unit_price``; anything else is ignored. ``instrument_name`` supplies the
    display name when present.

    ``year`` narrows the *result*, never the *input*: FIFO always walks the whole
    history, and only then are sales outside the year dropped. Filtering the rows
    up front would strip the buys that priced the remaining shares and every
    later sale would report a phantom profit at zero cost.

    Returns ``{"total_pnl", "total_proceeds", "total_cost", "by_ticker",
    "all_years", "unmatched_tickers"}`` with one ``by_ticker`` entry per ticker
    that sold something, ordered by result descending. A partial sale counts —
    shares that left the account are realized whether or not the position closed.

    Sells with no matching buy contribute their proceeds at zero cost. That is
    the honest reading: an export that begins after the purchase cannot price
    shares it never saw, and inventing a basis would manufacture a gain out of
    nothing. ``unmatched_tickers`` names them so the caller can disclose it.
    """
    lots: dict[str, list[list[float]]] = {}
    names: dict[str, str | None] = {}
    stats: dict[str, dict] = {}
    unmatched: set[str] = set()
    all_years: set[int] = set()

    trades = [
        op for op in operations
        if op.get("op_type") in (OP_BUY, OP_SELL) and op.get("ticker")
        and op.get("occurred_at") is not None
    ]

    for op in sorted(trades, key=_sort_key):
        ticker = op["ticker"]
        volume = float(op.get("volume") or 0.0)
        price = float(op.get("unit_price") or 0.0)
        lots.setdefault(ticker, [])
        if names.get(ticker) is None:
            names[ticker] = op.get("instrument_name")

        if op["op_type"] == OP_BUY:
            lots[ticker].append([volume, price])
            continue

        # Consume the lots even for a sale the year filter will drop: the shares
        # left the account, so the next in-scope sale must not be able to match
        # against them again.
        remaining = volume
        cost = 0.0
        for lot in lots[ticker]:
            if remaining <= _DUST:
                break
            take = min(lot[0], remaining)
            cost += take * lot[1]
            lot[0] -= take
            remaining -= take

        sold_year = op["occurred_at"].year
        all_years.add(sold_year)
        if year is not None and sold_year != year:
            continue
        if remaining > _DUST:
            unmatched.add(ticker)

        entry = stats.setdefault(
            ticker,
            {"ticker": ticker, "company_name": None, "shares_sold": 0.0,
             "proceeds": 0.0, "cost": 0.0, "sales": 0},
        )
        entry["shares_sold"] += volume
        entry["proceeds"] += volume * price
        entry["cost"] += cost
        entry["sales"] += 1

    by_ticker = []
    for ticker, entry in stats.items():
        entry["company_name"] = names.get(ticker)
        entry["pnl"] = entry["proceeds"] - entry["cost"]
        entry["pnl_pct"] = (
            entry["pnl"] / entry["cost"] * 100 if entry["cost"] > _DUST else None
        )
        by_ticker.append(entry)
    by_ticker.sort(key=lambda e: e["pnl"], reverse=True)

    return {
        "total_pnl": sum(e["pnl"] for e in by_ticker),
        "total_proceeds": sum(e["proceeds"] for e in by_ticker),
        "total_cost": sum(e["cost"] for e in by_ticker),
        "by_ticker": by_ticker,
        "all_years": sorted(all_years, reverse=True),
        "unmatched_tickers": sorted(unmatched),
    }
