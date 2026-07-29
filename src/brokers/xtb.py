"""Parser for XTB account exports.

Pure logic: no BigQuery, no FastAPI, no filesystem. Everything here turns bytes
or plain rows into dataclasses, so the numeric contract can be pinned by unit
tests before any endpoint exists.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from src.brokers.errors import BrokerParseError
from src.brokers.xlsx_reader import read_sheets

# Normalized operation types. The broker's own label is kept alongside in
# `raw_type`, so a future parser can map a different vocabulary onto the same
# set without the aggregation layer knowing which broker it came from.
OP_BUY = "buy"
OP_SELL = "sell"
OP_DIVIDEND = "dividend"
OP_WITHHOLDING_TAX = "withholding_tax"
OP_CASH = "cash"


@dataclass(frozen=True)
class Operation:
    """One normalized row of a broker's cash-operations sheet."""

    external_id: str
    op_type: str
    occurred_at: datetime
    amount_pln: float
    raw_type: str | None = None
    ticker: str | None = None
    instrument_name: str | None = None
    volume: float | None = None
    unit_price: float | None = None
    comment: str | None = None


@dataclass(frozen=True)
class Position:
    """An open position reconstructed from the transaction history."""

    ticker: str
    shares: float
    avg_buy_price: float
    company_name: str | None = None


@dataclass(frozen=True)
class BrokerImport:
    """Everything one broker export yields, before anything touches a database."""

    operations: list[Operation]
    positions: list[Position]
    dividends: list[Operation]
    closed_tickers: list[str]
    warnings: list[str]


@dataclass(frozen=True)
class _Lot:
    shares: float
    unit_price: float

# XTB writes one row per *fill*, not per order. In "OPEN BUY 9/11 @ 188.40" the
# 9 is what this row filled and the 11 is the whole order; a single order shows
# up as several rows whose filled volumes sum to the order size. Reading the
# second number would double every volume.
_TRADE_COMMENT_RE = re.compile(
    r"^(?:OPEN|CLOSE) BUY (?P<volume>[\d.]+)(?:/[\d.]+)? @ (?P<price>[\d.]+)$"
)


def parse_trade_comment(comment: str) -> tuple[float, float]:
    """Return ``(filled_volume, unit_price)`` from a trade row's comment."""
    match = _TRADE_COMMENT_RE.match((comment or "").strip())
    if match is None:
        raise BrokerParseError(f"unrecognised trade comment: {comment!r}")
    return float(match.group("volume")), float(match.group("price"))


# The broker's own vocabulary, observed in full across both real exports
# (573 rows). `Withholding tax` is the tax on dividends; `Free funds interest
# tax` is a different tax entirely and must NOT reach the dividend total —
# lumping them together turns Glowny's -435.28 into -437.00.
_TYPE_MAP = {
    "Stock purchase": OP_BUY,
    "Stock sell": OP_SELL,
    "Dividend": OP_DIVIDEND,
    "Withholding tax": OP_WITHHOLDING_TAX,
    "Deposit": OP_CASH,
    "IKZE deposit": OP_CASH,
    "Withdrawal": OP_CASH,
    "Transfer": OP_CASH,
    "Subaccount transfer": OP_CASH,
    "Free funds interest": OP_CASH,
    "Free funds interest tax": OP_CASH,
}

# Trailing summary row present in both sheets.
_TOTAL_LABEL = "Total"

# Types whose comment carries analytic value. For cash movements the comment is
# a payment-operator identifier ("Transfer in operation on account with id ...")
# and is dropped rather than stored.
_COMMENT_BEARING = frozenset({OP_BUY, OP_SELL, OP_DIVIDEND, OP_WITHHOLDING_TAX})

_PL_SUFFIX = ".PL"


def normalize_operations(rows: list[dict]) -> tuple[list[Operation], list[str]]:
    """Turn header-keyed sheet rows into normalized operations.

    Returns ``(operations, warnings)``. Warnings name what was deliberately not
    imported, so the preview can disclose it instead of silently shrinking the
    result.
    """
    operations: list[Operation] = []
    warnings: list[str] = []

    for row in rows:
        raw_type = (row.get("Type") or "").strip()
        if not raw_type or raw_type == _TOTAL_LABEL:
            continue

        ticker = (row.get("Ticker") or "").strip()
        if ticker and not ticker.endswith(_PL_SUFFIX):
            # Foreign instruments are out of scope: the comment prices them in
            # the listing currency while Amount is in PLN.
            warnings.append(
                f"Pominieto instrument zagraniczny {ticker} ({raw_type})"
            )
            continue

        op_type = _TYPE_MAP.get(raw_type)
        if op_type is None:
            warnings.append(f"Nieznany typ operacji: {raw_type}")
            continue

        volume: float | None = None
        unit_price: float | None = None
        if op_type in (OP_BUY, OP_SELL):
            volume, unit_price = parse_trade_comment(row.get("Comment") or "")

        comment = (row.get("Comment") or "").strip() or None
        operations.append(
            Operation(
                external_id=str(row.get("ID") or "").strip(),
                op_type=op_type,
                raw_type=raw_type,
                occurred_at=row["Time"],
                amount_pln=float(row.get("Amount") or 0.0),
                ticker=ticker or None,
                instrument_name=(row.get("Instrument") or "").strip() or None,
                volume=volume,
                unit_price=unit_price,
                comment=comment if op_type in _COMMENT_BEARING else None,
            )
        )

    return operations, warnings


_DIVIDEND_TYPES = frozenset({OP_DIVIDEND, OP_WITHHOLDING_TAX})


def extract_dividends(operations: list[Operation]) -> list[Operation]:
    """Return the cash-dividend events, gross and tax kept as separate rows.

    Deliberately does NOT pair a dividend with its withholding tax. On the real
    exports that pairing fails in 24 cases: the timestamps drift by seconds and
    a single payout is sometimes split across several rows. Totals are formed in
    SQL over these raw events instead.
    """
    return [op for op in operations if op.op_type in _DIVIDEND_TYPES]


def normalize_ticker(ticker: str) -> str:
    """Strip the exchange suffix XTB appends to Polish instruments."""
    return ticker[: -len(".PL")] if ticker.endswith(".PL") else ticker


# Volumes are floats (fractional shares are everywhere in these exports), so a
# lot that is fully consumed can land a hair above zero instead of exactly on it.
_DUST = 1e-9


def reconstruct_positions(
    operations: list[Operation],
) -> tuple[list[Position], list[str]]:
    """Rebuild open positions from a transaction history using FIFO.

    Returns ``(positions, closed_tickers)``. A ticker whose lots are fully
    consumed is reported as closed rather than as a zero-share position — the
    caller needs both lists and computing them separately would walk the history
    twice.

    ``avg_buy_price`` is ``remaining_cost / remaining_shares``. That contract is
    load-bearing: `_merge_positions_by_ticker` averages positions again by cost
    in "Wszystkie" mode, and P/L is computed elsewhere as
    ``(price - avg_buy_price) * shares``.
    """
    lots: dict[str, list[_Lot]] = {}
    names: dict[str, str | None] = {}
    seen: list[str] = []

    trades = [op for op in operations if op.op_type in (OP_BUY, OP_SELL) and op.ticker]
    # Rows are not guaranteed chronological in the export, and FIFO is only
    # meaningful against real time order.
    for op in sorted(trades, key=lambda o: o.occurred_at):
        ticker = normalize_ticker(op.ticker or "")
        if ticker not in lots:
            lots[ticker] = []
            seen.append(ticker)
        names.setdefault(ticker, op.instrument_name)
        volume = float(op.volume or 0.0)
        if op.op_type == OP_BUY:
            lots[ticker].append(_Lot(shares=volume, unit_price=float(op.unit_price or 0.0)))
        else:
            _consume_oldest(lots[ticker], volume)

    positions: list[Position] = []
    closed: list[str] = []
    for ticker in seen:
        remaining = [lot for lot in lots[ticker] if lot.shares > _DUST]
        shares = sum(lot.shares for lot in remaining)
        if shares <= _DUST:
            closed.append(ticker)
            continue
        cost = sum(lot.shares * lot.unit_price for lot in remaining)
        positions.append(
            Position(
                ticker=ticker,
                shares=shares,
                avg_buy_price=cost / shares,
                company_name=names.get(ticker),
            )
        )
    return positions, closed


_CASH_OPERATIONS_SHEET = "Cash Operations"


def parse_xtb_export(data: bytes) -> BrokerImport:
    """Parse a whole XTB `.xlsx` export into positions, dividends and operations."""
    sheets = read_sheets(data)
    if _CASH_OPERATIONS_SHEET not in sheets:
        raise BrokerParseError(
            f"plik nie zawiera arkusza '{_CASH_OPERATIONS_SHEET}'"
        )

    operations, warnings = normalize_operations(sheets[_CASH_OPERATIONS_SHEET])
    positions, closed_tickers = reconstruct_positions(operations)
    return BrokerImport(
        operations=operations,
        positions=positions,
        dividends=extract_dividends(operations),
        closed_tickers=closed_tickers,
        warnings=warnings,
    )


def _consume_oldest(lots: list[_Lot], volume: float) -> None:
    """Remove ``volume`` shares from the front of the lot queue, in place."""
    remaining = volume
    for index, lot in enumerate(lots):
        if remaining <= _DUST:
            break
        if lot.shares <= remaining + _DUST:
            remaining -= lot.shares
            lots[index] = _Lot(shares=0.0, unit_price=lot.unit_price)
        else:
            lots[index] = _Lot(shares=lot.shares - remaining, unit_price=lot.unit_price)
            remaining = 0.0
