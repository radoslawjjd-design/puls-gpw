"""Realized profit and loss from a broker's trade history.

Pure logic — no BigQuery, no FastAPI. Takes plain dicts so the same function
serves a freshly parsed export and rows read back out of user_broker_operations.

Deliberately separate from the dividend totals. A dividend is cash the company
paid; a realized result is what a sale actually returned against what the shares
cost. Adding them into one number would hide which of the two a portfolio is
actually living on.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.portfolio_lots import DUST as _DUST
from src.portfolio_lots import OP_BUY, OP_SELL, LotEvent, build_ledger

# PUL-120: `occurred_at` is a true UTC instant — the hour distribution of the real
# history is 7-15 UTC, which is the GPW session read in CEST. Periods are what the
# user saw on their statement, so they are Warsaw periods, not UTC ones. At year
# granularity the two disagree on one evening a year; at month granularity, twelve.
_WARSAW = ZoneInfo("Europe/Warsaw")


def _local(when: datetime) -> datetime:
    """The Warsaw-local view of a stored instant.

    A naive value is read as UTC rather than as the machine's clock: production
    always supplies tz-aware rows out of BigQuery, and letting a naive one inherit
    the host timezone would make the same input land in different periods on
    different machines.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(_WARSAW)


def _days_held(sold_at: datetime, acquired_at: datetime) -> int:
    """Whole days between acquiring shares and selling them.

    Counted between Warsaw-local *dates*, matching the period convention the
    rest of this module uses — the alternative, a raw timedelta on the stored
    instants, would make an afternoon buy and a morning sale one day apart read
    as zero.
    """
    return (_local(sold_at).date() - _local(acquired_at).date()).days


def compute_realized_pnl(
    operations: list[dict], year: int | None = None, month: int | None = None
) -> dict:
    """Match sells against buys FIFO and total what each ticker actually returned.

    Input rows need ``ticker``, ``op_type``, ``occurred_at``, ``volume`` and
    ``unit_price``; anything else is ignored. ``instrument_name`` supplies the
    display name when present.

    ``year`` and ``month`` narrow the *result*, never the *input*: FIFO always
    walks the whole history, and only then are sales outside the period dropped.
    Filtering the rows up front would strip the buys that priced the remaining
    shares and every later sale would report a phantom profit at zero cost.

    The two are independent. ``month`` without ``year`` means that month in every
    year on record — a coherent question the selectors let the user ask.

    Returns ``{"total_pnl", "total_proceeds", "total_cost", "by_ticker",
    "all_years", "unmatched_tickers"}`` with one ``by_ticker`` entry per ticker
    that sold something, ordered by result descending. A partial sale counts —
    shares that left the account are realized whether or not the position closed.

    Sells with no matching buy contribute their proceeds at zero cost. That is
    the honest reading: an export that begins after the purchase cannot price
    shares it never saw, and inventing a basis would manufacture a gain out of
    nothing. ``unmatched_tickers`` names them so the caller can disclose it.

    Each entry also carries ``days_held_weighted`` (volume-weighted mean holding
    period over the ticker's in-period matches) and ``days_held_max`` (from the
    oldest lot the sales consumed). Both are ``None`` when the in-period sales
    matched no lot at all — shares sold with no recorded purchase have no
    acquisition date, and reporting them as held zero days would be a fabricated
    number rather than a missing one.

    The lot walk itself lives in ``src.portfolio_lots``; this module owns only
    the reporting period. That split is deliberate: FIFO must see every trade,
    and a ledger that knew about ``year``/``month`` would invite filtering the
    input.
    """
    stats: dict[str, dict] = {}
    unmatched: set[str] = set()
    all_years: set[int] = set()

    ledgers = build_ledger(
        LotEvent(
            ticker=op["ticker"],
            op_type=op["op_type"],
            occurred_at=op["occurred_at"],
            volume=float(op.get("volume") or 0.0),
            unit_price=float(op.get("unit_price") or 0.0),
            instrument_name=op.get("instrument_name"),
        )
        for op in operations
        if op.get("op_type") in (OP_BUY, OP_SELL) and op.get("ticker")
        and op.get("occurred_at") is not None
    )

    # Sales are walked in chronological order across every ticker, not
    # ticker-by-ticker, so that two tickers finishing on the same result keep the
    # order the old single-pass loop gave them — `by_ticker`'s sort is stable, so
    # insertion order is what breaks a tie.
    sales = sorted(
        ((ticker, sale) for ticker, ledger in ledgers.items() for sale in ledger.sales),
        key=lambda pair: pair[1].sold_at,
    )

    for ticker, sale in sales:
        # The ledger has already consumed the lots for every sale, including the
        # ones this period filter is about to drop — the shares left the account,
        # so a later in-scope sale must not be able to match against them again.
        sold_at = _local(sale.sold_at)
        all_years.add(sold_at.year)
        if year is not None and sold_at.year != year:
            continue
        if month is not None and sold_at.month != month:
            continue
        if sale.uncovered > _DUST:
            unmatched.add(ticker)

        entry = stats.setdefault(
            ticker,
            {"ticker": ticker, "company_name": None, "shares_sold": 0.0,
             "proceeds": 0.0, "cost": 0.0, "sales": 0,
             "_held_volume": 0.0, "_held_weighted": 0.0, "_held_max": None},
        )
        # Volume and proceeds come from the SALE, cost from the matched lots.
        # Taking all three from the matches would drop the uncovered part of a
        # partially priced sale and quietly understate what the account received.
        entry["shares_sold"] += sale.volume
        entry["proceeds"] += sale.proceeds
        entry["cost"] += sale.cost
        entry["sales"] += 1

        for match in sale.matches:
            days = _days_held(sale.sold_at, match.acquired_at)
            entry["_held_volume"] += match.volume
            entry["_held_weighted"] += match.volume * days
            entry["_held_max"] = (
                days if entry["_held_max"] is None else max(entry["_held_max"], days)
            )

    by_ticker = []
    for ticker, entry in stats.items():
        entry["company_name"] = ledgers[ticker].instrument_name
        entry["pnl"] = entry["proceeds"] - entry["cost"]
        entry["pnl_pct"] = (
            entry["pnl"] / entry["cost"] * 100 if entry["cost"] > _DUST else None
        )
        held_volume = entry.pop("_held_volume")
        weighted = entry.pop("_held_weighted")
        # Absent, not zero: shares sold with no recorded purchase carry no
        # acquisition date, and "held 0 days" is a fabricated answer.
        entry["days_held_weighted"] = (
            weighted / held_volume if held_volume > _DUST else None
        )
        entry["days_held_max"] = entry.pop("_held_max")
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
