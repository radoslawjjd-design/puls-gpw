"""Tests for the dated FIFO lot ledger (PUL-114 part 1).

Pure-function tests: no BigQuery, no HTTP. This module is the single engine
behind every lot consumption in the codebase, so the cases below are deliberately
the ones the two implementations it replaces disagreed on.
"""

from datetime import datetime

import pytest

from src.portfolio_lots import LotEvent, build_ledger


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
