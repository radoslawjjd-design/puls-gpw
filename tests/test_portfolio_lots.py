"""Tests for the dated FIFO lot ledger (PUL-114 part 1).

Pure-function tests: no BigQuery, no HTTP. This module is the single engine
behind every lot consumption in the codebase, so the cases below are deliberately
the ones the two implementations it replaces disagreed on.
"""

import ast
import pathlib
from datetime import date, datetime

import pytest

from src.portfolio_lots import (
    LotEvent,
    StoredPosition,
    basis_segments,
    build_ledger,
    first_open_lot_dates,
)


def test_the_ledger_imports_nothing_but_the_standard_library():
    """`src/brokers` now depends on this module, and
    tests/test_brokers_xtb.py::test_parser_package_imports_no_data_or_web_layer
    only inspects the parser package's *direct* imports. Without this guard the
    parser's purity — what lets the golden dataset run with no infrastructure —
    could be broken one hop away, from a file that test never opens."""
    forbidden = ("db", "fastapi", "google", "starlette", "pydantic")
    path = pathlib.Path(__file__).resolve().parents[1] / "src" / "portfolio_lots.py"

    offenders = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        offenders += [n for n in names if n.split(".")[0] in forbidden]

    assert offenders == []


def _buy(ticker, when, volume, price, name=None):
    return LotEvent(ticker=ticker, op_type="buy", occurred_at=when,
                    volume=volume, unit_price=price, instrument_name=name)


def _sell(ticker, when, volume, price, name=None):
    return LotEvent(ticker=ticker, op_type="sell", occurred_at=when,
                    volume=volume, unit_price=price, instrument_name=name)


def test_a_sale_consumes_the_oldest_lot_first():
    """FIFO is the whole point: XTB's own tax method, and the reason the basis
    of a half-sold position is not its average purchase price."""
    ledger = build_ledger([
        _buy("KRU", datetime(2025, 1, 10), 10, 300.0),
        _buy("KRU", datetime(2025, 6, 10), 10, 400.0),
        _sell("KRU", datetime(2025, 9, 10), 10, 500.0),
    ])["KRU"]

    assert len(ledger.sales) == 1
    matches = ledger.sales[0].matches
    assert len(matches) == 1
    assert matches[0].acquired_at == datetime(2025, 1, 10)
    assert matches[0].cost == pytest.approx(3000.0), "the cheaper, older lot goes first"
    assert ledger.open_shares == pytest.approx(10.0)
    assert ledger.open_cost == pytest.approx(4000.0)


def test_a_sale_spanning_two_lots_reports_one_sale_with_two_matches():
    """The shape that protects the caller: a sale is one sale however many lots
    it eats. Counting matches instead would report three sales for one."""
    ledger = build_ledger([
        _buy("PAS", datetime(2025, 1, 1), 4, 100.0),
        _buy("PAS", datetime(2025, 2, 1), 4, 200.0),
        _buy("PAS", datetime(2025, 3, 1), 4, 300.0),
        _sell("PAS", datetime(2025, 4, 1), 10, 250.0),
    ])["PAS"]

    sale = ledger.sales[0]
    assert len(ledger.sales) == 1
    assert len(sale.matches) == 3
    assert [m.acquired_at for m in sale.matches] == [
        datetime(2025, 1, 1), datetime(2025, 2, 1), datetime(2025, 3, 1)
    ]
    assert sale.matches[-1].volume == pytest.approx(2.0), "the third lot is only part-eaten"
    assert sale.cost == pytest.approx(4 * 100.0 + 4 * 200.0 + 2 * 300.0)
    assert ledger.open_shares == pytest.approx(2.0)


def test_a_sale_carries_its_whole_volume_even_when_lots_cover_only_part_of_it():
    """The trap this shape exists to make unreachable. Proceeds are what left the
    account; cost is what the lots could price. Deriving volume from the matches
    would understate proceeds and P&L on every partially covered sale."""
    ledger = build_ledger([
        _buy("BAC", datetime(2025, 3, 1), 4, 10.0),
        _sell("BAC", datetime(2025, 5, 1), 10, 25.0),
    ])["BAC"]

    sale = ledger.sales[0]
    assert sale.volume == pytest.approx(10.0), "all ten shares left the account"
    assert sale.proceeds == pytest.approx(250.0), "and all ten were paid for"
    assert sale.cost == pytest.approx(40.0), "but only four could be priced"
    assert sale.uncovered == pytest.approx(6.0)
    assert ledger.uncovered == pytest.approx(6.0)


def test_lots_are_floored_at_zero_never_negative():
    """An export window that starts after a purchase oversells. Letting the lot
    go negative would credit the next buy against a phantom debt."""
    ledger = build_ledger([
        _buy("S2B", datetime(2025, 1, 1), 2, 50.0),
        _sell("S2B", datetime(2025, 2, 1), 5, 60.0),
        _buy("S2B", datetime(2025, 3, 1), 3, 70.0),
    ])["S2B"]

    assert ledger.open_shares == pytest.approx(3.0), "the later buy stands on its own"
    assert ledger.open_cost == pytest.approx(210.0)
    assert ledger.uncovered == pytest.approx(3.0)


def test_a_ticker_sold_to_zero_and_rebought_does_not_reuse_consumed_lots():
    """PUL-114 verification criterion. A consumed lot is gone; the re-buy is
    priced by its own money, not by what the position used to cost."""
    ledger = build_ledger([
        _buy("PZU", datetime(2025, 1, 1), 10, 50.0),
        _sell("PZU", datetime(2025, 2, 1), 10, 55.0),
        _buy("PZU", datetime(2025, 3, 1), 10, 60.0),
    ])["PZU"]

    assert ledger.open_shares == pytest.approx(10.0)
    assert ledger.open_cost == pytest.approx(600.0), "the 50.00 lot is spent, not reusable"
    assert ledger.uncovered == pytest.approx(0.0)
    assert len(ledger.open_lots) == 1
    assert ledger.open_lots[0].occurred_at == datetime(2025, 3, 1)


def test_a_buy_sharing_a_timestamp_with_its_sell_is_applied_first():
    """A fill and its closing fill occasionally share a timestamp to the
    microsecond. Consuming a lot not yet recorded drops the basis on the floor."""
    when = datetime(2025, 5, 5, 10, 0, 0)
    ledger = build_ledger([
        _sell("SNT", when, 5, 60.0),
        _buy("SNT", when, 5, 50.0),
    ])["SNT"]

    assert ledger.sales[0].cost == pytest.approx(250.0)
    assert ledger.uncovered == pytest.approx(0.0)


def test_events_out_of_order_are_sorted_before_consumption():
    """Rows are not guaranteed chronological, and FIFO is only meaningful
    against real time order."""
    ledger = build_ledger([
        _sell("TOA", datetime(2025, 9, 1), 10, 12.0),
        _buy("TOA", datetime(2025, 6, 1), 10, 11.0),
        _buy("TOA", datetime(2025, 1, 1), 10, 10.0),
    ])["TOA"]

    assert ledger.sales[0].matches[0].acquired_at == datetime(2025, 1, 1)
    assert ledger.open_cost == pytest.approx(110.0)


def test_the_instrument_name_is_the_first_non_null_one():
    """xtb.py's setdefault locks in None when the first row carries no name;
    the ledger keeps looking, so an import no longer loses the company name."""
    ledger = build_ledger([
        _buy("DIG", datetime(2025, 1, 1), 5, 60.0, name=None),
        _buy("DIG", datetime(2025, 2, 1), 5, 70.0, name="Digitree"),
        _buy("DIG", datetime(2025, 3, 1), 5, 80.0, name="Ignored Later Name"),
    ])["DIG"]

    assert ledger.instrument_name == "Digitree"


def test_a_fully_consumed_lot_does_not_survive_as_dust():
    """Fractional shares are everywhere in these exports, so a spent lot can land
    a hair above zero. A dust lot would render as a phantom position."""
    ledger = build_ledger([
        _buy("VOT", datetime(2025, 1, 1), 0.1 + 0.2, 47.0),
        _sell("VOT", datetime(2025, 2, 1), 0.3, 50.0),
    ])["VOT"]

    assert ledger.open_lots == []
    assert ledger.open_shares == pytest.approx(0.0)
    assert ledger.uncovered == pytest.approx(0.0, abs=1e-9)


def test_tickers_keep_first_appearance_order_and_are_not_normalized():
    """reconstruct_positions relies on first-appearance order, and `.PL`
    stripping belongs to the XTB adapter — stored rows are already normalized."""
    ledger = build_ledger([
        _buy("ZZZ", datetime(2025, 1, 1), 1, 10.0),
        _buy("AAA.PL", datetime(2025, 2, 1), 1, 20.0),
    ])

    assert list(ledger) == ["ZZZ", "AAA.PL"]


def test_the_basis_steps_down_to_the_rebuy_instead_of_reaching_back_over_it():
    """PUL-114's whole point. A ticker sold to zero and bought again is priced by
    today's basis on *every* historical day, including days whose lots were
    already spent. Four production tickers are in this state (CBF, KRU, SNT, XTB)
    and nothing corrects them today: the live position row means the SQL fallback
    is never reached."""
    events = {"W1": [
        _buy("PZU", datetime(2025, 1, 1), 10, 50.0),
        _sell("PZU", datetime(2025, 2, 1), 10, 55.0),
        _buy("PZU", datetime(2025, 3, 1), 10, 60.0),
    ]}
    positions = {("W1", "PZU"): StoredPosition(shares=10.0, avg_buy_price=60.0)}

    segments = basis_segments(events, positions)

    assert [(s.valid_from, s.basis) for s in segments] == [
        (date(2025, 1, 1), pytest.approx(50.0)),
        (date(2025, 3, 1), pytest.approx(60.0)),
    ], "January must cost 50.00 — the flat basis charges it 60.00, the re-buy's price"
    assert all(s.ticker == "PZU" and s.portfolio_id == "W1" for s in segments)


def test_the_day_the_lots_run_out_carries_no_segment_at_all():
    """A spent position must not emit a zero-cost segment; zero basis renders as
    100% profit. The caller decides what an unpriceable holding means."""
    events = {"W1": [
        _buy("TOA", datetime(2025, 1, 1), 5, 20.0),
        _sell("TOA", datetime(2025, 4, 1), 5, 25.0),
    ]}

    segments = basis_segments(events, {})

    assert [s.valid_from for s in segments] == [date(2025, 1, 1)]
    assert segments[0].basis == pytest.approx(20.0)


def test_a_ticker_with_no_position_row_is_still_priced_from_its_own_buys():
    """The case `ops_basis` existed for — sold to zero, position row deleted at
    import. The ledger serves it with a *dated* cost instead of one all-buys
    average, which production measured wrong for 8 of 20 such tickers."""
    events = {"W1": [
        _buy("BAC", datetime(2025, 1, 1), 10, 30.0),
        _buy("BAC", datetime(2025, 3, 1), 10, 40.0),
        _sell("BAC", datetime(2025, 6, 1), 20, 35.0),
    ]}

    segments = basis_segments(events, {})

    assert [(s.valid_from, s.basis) for s in segments] == [
        (date(2025, 1, 1), pytest.approx(30.0)),
        (date(2025, 3, 1), pytest.approx(35.0)),
    ], "March holds both lots at 35.00; January must not borrow the later, dearer lot"


def test_residual_shares_are_priced_at_the_stored_basis_never_at_zero():
    """Shares held but covered by no lot — a spin-off, an in-kind transfer, an
    export window opening after the purchase. Charging them nothing would report
    the whole position as profit."""
    events = {"W1": [_buy("S2B", datetime(2025, 3, 1), 4, 10.0)]}
    positions = {("W1", "S2B"): StoredPosition(shares=10.0, avg_buy_price=25.0)}

    segments = basis_segments(events, positions)

    # (4 x 10.00 + 6 x 25.00) / 10 shares
    assert segments[0].basis == pytest.approx(19.0)


def test_an_unpriced_residual_falls_back_to_the_ledger_not_to_zero():
    """Plan review F3. No position row means no stored price, so a residual left
    by an oversell has nothing to charge it with. It inherits the basis of the
    shares the ledger *can* explain — dividing the lot cost across every share
    would under-price it by 60% here, and reads as profit that never happened."""
    events = {"W1": [
        _buy("LPP", datetime(2025, 1, 1), 4, 10.0),
        _sell("LPP", datetime(2025, 5, 1), 10, 25.0),
    ]}

    segments = basis_segments(events, {})

    assert segments[0].valid_from == date(2025, 1, 1)
    assert segments[0].basis == pytest.approx(10.0), "the lots' own price, not 40/10"
    assert [s.valid_from for s in segments] == [date(2025, 1, 1)], (
        "by May the ledger holds nothing, so it offers no price at all"
    )


def test_a_ticker_emits_no_segment_on_a_day_its_own_basis_did_not_move():
    """A step function should step. Every ticker in a wallet would otherwise
    re-emit its unchanged basis on every other ticker's trading day, inflating
    the query parameter several-fold and making 'a segment' mean nothing."""
    events = {"W1": [
        _buy("AAA", datetime(2025, 1, 1), 10, 100.0),
        _buy("BBB", datetime(2025, 2, 1), 10, 200.0),
        _buy("AAA", datetime(2025, 3, 1), 10, 300.0),
    ]}
    positions = {
        ("W1", "AAA"): StoredPosition(shares=20.0, avg_buy_price=200.0),
        ("W1", "BBB"): StoredPosition(shares=10.0, avg_buy_price=200.0),
    }

    segments = basis_segments(events, positions)

    assert [(s.ticker, s.valid_from, s.basis) for s in segments] == [
        ("AAA", date(2025, 1, 1), pytest.approx(100.0)),
        ("BBB", date(2025, 2, 1), pytest.approx(200.0)),
        ("AAA", date(2025, 3, 1), pytest.approx(200.0)),
    ], "AAA must not reappear on BBB's day, nor BBB on AAA's"


def test_two_wallets_holding_the_same_ticker_do_not_share_a_lot_stream():
    """In the 'Wszystkie' view both wallets are in scope at once. Merging them
    into one FIFO stream would let a sale in one wallet consume the other's lot."""
    events = {
        "W1": [_buy("KRU", datetime(2025, 1, 1), 10, 100.0)],
        "W2": [_buy("KRU", datetime(2025, 1, 1), 10, 200.0)],
    }
    positions = {
        ("W1", "KRU"): StoredPosition(shares=10.0, avg_buy_price=100.0),
        ("W2", "KRU"): StoredPosition(shares=10.0, avg_buy_price=200.0),
    }

    by_wallet = {s.portfolio_id: s.basis for s in basis_segments(events, positions)}

    assert by_wallet == {"W1": pytest.approx(100.0), "W2": pytest.approx(200.0)}


def test_the_holding_period_starts_at_the_oldest_open_lot_not_the_first_buy_ever():
    """PUL-123 part 2's input. Three of thirteen live production tickers differ
    here — CBF by 424 days, XTB by 332, SNT by 249. MIN(buy) would report a
    holding period whose shares the owner sold long ago."""
    events = {"W1": [
        _buy("CBF", datetime(2025, 5, 19), 10, 150.0),
        _sell("CBF", datetime(2025, 7, 2), 10, 160.0),
        _buy("CBF", datetime(2026, 7, 17), 11, 188.4),
    ]}

    assert first_open_lot_dates(events) == {("W1", "CBF"): date(2026, 7, 17)}


def test_a_ticker_with_nothing_open_reports_no_acquisition_date():
    """Sold to zero: there is no share to have been holding, so there is no
    holding period. None is the answer; a date would be fabricated."""
    events = {"W1": [
        _buy("DIA", datetime(2025, 1, 1), 5, 170.0),
        _sell("DIA", datetime(2025, 9, 1), 5, 171.0),
    ]}

    assert first_open_lot_dates(events) == {}


def test_a_ticker_that_only_ever_sold_still_reports_its_sale():
    """An export beginning after the purchase. The sale is real and its proceeds
    are real; only the basis is unknowable."""
    ledger = build_ledger([_sell("LPP", datetime(2025, 4, 1), 2, 14000.0)])["LPP"]

    assert len(ledger.sales) == 1
    assert ledger.sales[0].proceeds == pytest.approx(28000.0)
    assert ledger.sales[0].cost == pytest.approx(0.0)
    assert ledger.sales[0].matches == ()
    assert ledger.uncovered == pytest.approx(2.0)
    assert ledger.open_lots == []
