"""The dated FIFO lot ledger — the single engine behind every lot consumption.

Pure logic: no BigQuery, no FastAPI, no I/O. Callers hand in a normalized event
stream and read back what the lots say.

Three implementations of this used to exist. `src/brokers/xtb.py` ran FIFO at
import to produce `avg_buy_price`, `src/portfolio_realized.py` ran a second,
subtly different one over the stored operations, and `db/bigquery.py` computed a
plain SQL average. The first two disagreed on same-instant ordering, on what
happens to a sale the lots cannot cover, and on how an instrument name is
resolved. This module settles all three, and adds the thing none of them had:
lots that remember **when** they were acquired.

Two shape decisions are load-bearing.

**A sale is a first-class object, and it carries its own volume.** The matches
hang off it. The tempting shape — a flat list of sell/lot matches — quietly
loses the part of a sale the lots could not price, so proceeds and P&L come out
low on every partially covered sale, and a sale eating three lots reads as three
sales. Here the caller cannot reach a sale's volume except through the sale.

**The ledger reports what it could not cover; it does not decide what to do
about it.** `user_broker_operations` is a complete record of the movements the
broker saw, not of holdings — spin-offs, in-kind transfers and hand-entered
positions are structurally absent. So the ledger cannot know a position's whole
history and must not pretend to. It surfaces `uncovered`; the caller reconciles
that against whatever stored snapshot it has. That boundary is what keeps "no lot
found" from collapsing into a zero basis, which would read as 100% profit.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

OP_BUY = "buy"
OP_SELL = "sell"

# Fractional shares are everywhere in these exports, so a lot that is fully
# consumed can land a hair above zero rather than exactly on it. Single
# definition for the whole codebase — xtb.py and portfolio_realized.py both used
# to carry their own copy.
DUST = 1e-9


@dataclass(frozen=True)
class LotEvent:
    """One share-moving operation, normalized.

    Tickers are taken exactly as given. Normalizing here would couple the ledger
    to one broker's suffix convention; rows stored in `user_broker_operations`
    are already normalized at write time, and the XTB adapter normalizes before
    building its events.
    """

    ticker: str
    op_type: str
    occurred_at: datetime
    volume: float
    unit_price: float
    instrument_name: str | None = None


@dataclass(frozen=True)
class OpenLot:
    """Shares still held, at what they cost and when they were acquired."""

    volume: float
    price: float
    occurred_at: datetime

    @property
    def cost(self) -> float:
        return self.volume * self.price


@dataclass(frozen=True)
class LotMatch:
    """The part of one sale drawn from one lot."""

    acquired_at: datetime
    volume: float
    cost: float


@dataclass(frozen=True)
class Sale:
    """A sale, with the lots it consumed hanging off it.

    ``volume`` and ``proceeds`` describe what actually left the account, whether
    or not the lots could price it. ``cost`` describes only what could be priced.
    Keeping those separate is the point of this class.
    """

    ticker: str
    sold_at: datetime
    volume: float
    unit_price: float
    matches: tuple[LotMatch, ...]
    uncovered: float

    @property
    def proceeds(self) -> float:
        return self.volume * self.unit_price

    @property
    def cost(self) -> float:
        return sum(match.cost for match in self.matches)


@dataclass
class TickerLedger:
    """Everything the lots say about one ticker."""

    ticker: str
    instrument_name: str | None = None
    open_lots: list[OpenLot] = field(default_factory=list)
    sales: list[Sale] = field(default_factory=list)

    @property
    def open_shares(self) -> float:
        return sum(lot.volume for lot in self.open_lots)

    @property
    def open_cost(self) -> float:
        return sum(lot.cost for lot in self.open_lots)

    @property
    def uncovered(self) -> float:
        """Shares sold that no lot could price, across every sale."""
        return sum(sale.uncovered for sale in self.sales)


def _sort_key(event: LotEvent) -> tuple[datetime, int]:
    """Chronological, with buy-before-sell as the tiebreak.

    Rows are not guaranteed to arrive in order and FIFO is only meaningful
    against real time order. Same-timestamp rows are broken by buy-before-sell: a
    fill and its closing fill occasionally share a timestamp to the microsecond,
    and consuming a lot that has not been recorded yet would drop the cost basis
    on the floor.
    """
    return (event.occurred_at, 0 if event.op_type == OP_BUY else 1)


def build_ledger(events: Iterable[LotEvent]) -> dict[str, TickerLedger]:
    """Replay every event and return one ledger per ticker.

    Keys are ordered by **first appearance in chronological order**, which is
    what `reconstruct_positions` relies on to report positions and closed tickers
    in a stable order.

    Events without a ticker, without a timestamp, or of any type other than
    buy/sell are dropped — the same filter both callers applied before they had a
    ledger to hand them to.
    """
    trades = [
        event for event in events
        if event.op_type in (OP_BUY, OP_SELL)
        and event.ticker
        and event.occurred_at is not None
    ]

    ledgers: dict[str, TickerLedger] = {}
    # Lots are mutated during consumption, so they are held as [volume, price,
    # acquired_at] lists here and frozen into OpenLots once the walk is done.
    working: dict[str, list[list]] = {}

    for event in sorted(trades, key=_sort_key):
        ticker = event.ticker
        ledger = ledgers.get(ticker)
        if ledger is None:
            ledger = ledgers[ticker] = TickerLedger(ticker=ticker)
            working[ticker] = []
        if ledger.instrument_name is None:
            # First *non-None* wins. xtb.py used setdefault, which locks in None
            # when the earliest row happens to carry no name and loses the
            # company name for that ticker permanently.
            ledger.instrument_name = event.instrument_name

        volume = float(event.volume or 0.0)
        price = float(event.unit_price or 0.0)

        if event.op_type == OP_BUY:
            working[ticker].append([volume, price, event.occurred_at])
            continue

        remaining = volume
        matches: list[LotMatch] = []
        for lot in working[ticker]:
            if remaining <= DUST:
                break
            if lot[0] <= DUST:
                continue
            taken = min(lot[0], remaining)
            matches.append(LotMatch(acquired_at=lot[2], volume=taken, cost=taken * lot[1]))
            lot[0] -= taken
            remaining -= taken

        ledger.sales.append(Sale(
            ticker=ticker,
            sold_at=event.occurred_at,
            volume=volume,
            unit_price=price,
            matches=tuple(matches),
            # Floored at zero, never negative: an export window that starts after
            # a purchase oversells, and a negative lot would credit the next buy
            # against a debt that does not exist.
            uncovered=remaining if remaining > DUST else 0.0,
        ))

    for ticker, lots in working.items():
        ledgers[ticker].open_lots = [
            OpenLot(volume=lot[0], price=lot[1], occurred_at=lot[2])
            for lot in lots if lot[0] > DUST
        ]

    return ledgers
