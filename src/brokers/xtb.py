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
from src.portfolio_lots import DUST, LotEvent, build_ledger

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
    # Uninvested cash sitting on the account, in PLN. None when the export gives
    # no way to know it — which is not the same as zero and must not render as
    # "you have no cash".
    cash_pln: float | None = None


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

    The lot walk lives in ``src.portfolio_lots`` (PUL-114). Only the broker-facing
    part stays here: `.PL` stripping belongs to the export's vocabulary, not to
    the ledger, which takes tickers exactly as given because rows already stored
    in `user_broker_operations` are normalized at write time.

    One behaviour change came with the move. `company_name` is now the first
    *non-None* instrument name for the ticker rather than the first value seen —
    the old `setdefault` locked in `None` whenever the earliest row happened to
    carry no name, losing that ticker's company name permanently.
    """
    ledgers = build_ledger(
        LotEvent(
            ticker=normalize_ticker(op.ticker or ""),
            op_type=op.op_type,
            occurred_at=op.occurred_at,
            volume=float(op.volume or 0.0),
            unit_price=float(op.unit_price or 0.0),
            instrument_name=op.instrument_name,
        )
        for op in operations
        if op.op_type in (OP_BUY, OP_SELL) and op.ticker
    )

    positions: list[Position] = []
    closed: list[str] = []
    for ticker, ledger in ledgers.items():
        shares = ledger.open_shares
        if shares <= DUST:
            closed.append(ticker)
            continue
        positions.append(
            Position(
                ticker=ticker,
                shares=shares,
                avg_buy_price=ledger.open_cost / shares,
                company_name=ledger.instrument_name,
            )
        )
    return positions, closed


_CASH_OPERATIONS_SHEET = "Cash Operations"


def extract_cash_balance(rows: list[dict]) -> float | None:
    """Return the account's free cash from the sheet's trailing `Total` row.

    XTB closes the sheet with a Total whose Amount is the net of every cash
    movement — deposits, trades, dividends, taxes, interest — which is exactly
    the uninvested balance. That row is the broker's own figure, and it is the
    one to use: summing the *imported* operations instead gives a different
    number, because the parser drops foreign instruments on purpose (measured on
    the real exports: Glowny 84.03 from the Total against 143.94 from the parsed
    rows, a 59.91 gap that is entirely foreign trades).

    Returns None when there is no Total row — an unknown balance, not a zero one.
    """
    for row in reversed(rows):
        if (row.get("Type") or "").strip() != _TOTAL_LABEL:
            continue
        amount = row.get("Amount")
        if amount is None:
            return None
        try:
            return float(amount)
        except (TypeError, ValueError):
            return None
    return None


def parse_xtb_export(data: bytes) -> BrokerImport:
    """Parse a whole XTB `.xlsx` export into positions, dividends and operations."""
    sheets = read_sheets(data)
    if _CASH_OPERATIONS_SHEET not in sheets:
        raise BrokerParseError(
            f"plik nie zawiera arkusza '{_CASH_OPERATIONS_SHEET}'"
        )

    rows = sheets[_CASH_OPERATIONS_SHEET]
    operations, warnings = normalize_operations(rows)
    positions, closed_tickers = reconstruct_positions(operations)
    return BrokerImport(
        operations=operations,
        positions=positions,
        dividends=extract_dividends(operations),
        closed_tickers=closed_tickers,
        warnings=warnings,
        cash_pln=extract_cash_balance(rows),
    )
