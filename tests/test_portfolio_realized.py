"""Tests for the realized profit/loss engine (PUL-95 Phase 8).

Pure-function tests: no BigQuery, no HTTP. The numeric contract is what matters
here, because this is the first figure in the app the user could check against
their own tax return.
"""

from datetime import datetime

import pytest

from src.portfolio_realized import compute_realized_pnl


def _buy(ticker, when, volume, price, name=None):
    return {"ticker": ticker, "op_type": "buy", "occurred_at": when,
            "volume": volume, "unit_price": price, "instrument_name": name}


def _sell(ticker, when, volume, price):
    return {"ticker": ticker, "op_type": "sell", "occurred_at": when,
            "volume": volume, "unit_price": price}


def test_a_full_sale_realizes_the_whole_difference():
    ops = [
        _buy("PKO", datetime(2025, 1, 10), 100, 40.0, "PKO BP"),
        _sell("PKO", datetime(2025, 6, 10), 100, 50.0),
    ]
    result = compute_realized_pnl(ops)
    entry = result["by_ticker"][0]
    assert entry["ticker"] == "PKO"
    assert entry["company_name"] == "PKO BP"
    assert entry["proceeds"] == pytest.approx(5000.0)
    assert entry["cost"] == pytest.approx(4000.0)
    assert entry["pnl"] == pytest.approx(1000.0)
    assert entry["pnl_pct"] == pytest.approx(25.0)
    assert result["total_pnl"] == pytest.approx(1000.0)


def test_partial_sale_realizes_only_what_left_the_account():
    """A position still held is not a reason to report nothing — the sold half is
    realized whether or not the rest is."""
    ops = [
        _buy("CDR", datetime(2025, 1, 1), 10, 100.0),
        _sell("CDR", datetime(2025, 2, 1), 4, 150.0),
    ]
    entry = compute_realized_pnl(ops)["by_ticker"][0]
    assert entry["shares_sold"] == pytest.approx(4)
    assert entry["pnl"] == pytest.approx(200.0)  # 4 x (150 - 100)


def test_fifo_consumes_the_oldest_lot_first():
    """LIFO on this input would report 100, FIFO reports 300 — the whole point."""
    ops = [
        _buy("ELT", datetime(2025, 1, 1), 10, 20.0),
        _buy("ELT", datetime(2025, 3, 1), 10, 40.0),
        _sell("ELT", datetime(2025, 6, 1), 10, 50.0),
    ]
    entry = compute_realized_pnl(ops)["by_ticker"][0]
    assert entry["cost"] == pytest.approx(200.0)
    assert entry["pnl"] == pytest.approx(300.0)


def test_rows_out_of_order_are_sorted_before_matching():
    """The export is not guaranteed chronological and FIFO only means something
    against real time order."""
    ops = [
        _sell("ELT", datetime(2025, 6, 1), 10, 50.0),
        _buy("ELT", datetime(2025, 3, 1), 10, 40.0),
        _buy("ELT", datetime(2025, 1, 1), 10, 20.0),
    ]
    entry = compute_realized_pnl(ops)["by_ticker"][0]
    assert entry["cost"] == pytest.approx(200.0)


def test_a_buy_sharing_a_timestamp_with_its_sell_is_applied_first():
    """Fills sometimes land on the same microsecond. Consuming a lot that has not
    been recorded yet drops the cost basis and invents a 100% gain."""
    when = datetime(2025, 5, 5, 10, 0, 0)
    ops = [_sell("SNT", when, 5, 60.0), _buy("SNT", when, 5, 50.0)]
    result = compute_realized_pnl(ops)
    entry = result["by_ticker"][0]
    assert entry["cost"] == pytest.approx(250.0)
    assert entry["pnl"] == pytest.approx(50.0)
    assert result["unmatched_tickers"] == []


def test_a_sale_with_no_recorded_purchase_is_flagged_not_priced():
    """An export starting after the purchase cannot know the basis. Reporting the
    proceeds as pure profit without saying so would be a lie with a number on it."""
    ops = [_sell("S2B", datetime(2025, 4, 1), 4, 25.0)]
    result = compute_realized_pnl(ops)
    assert result["unmatched_tickers"] == ["S2B"]
    entry = result["by_ticker"][0]
    assert entry["cost"] == pytest.approx(0.0)
    assert entry["pnl_pct"] is None  # no basis → no percentage, not a division by zero


def test_a_ticker_only_ever_bought_is_absent():
    """This is the realized view. An open position has realized nothing."""
    ops = [_buy("KRU", datetime(2025, 1, 1), 10, 300.0)]
    result = compute_realized_pnl(ops)
    assert result["by_ticker"] == []
    assert result["total_pnl"] == pytest.approx(0.0)


def test_year_filters_the_result_but_fifo_still_walks_the_whole_history():
    """The trap: filtering rows by year first strips the buys that priced the
    remaining shares, and every later sale then reports profit at zero cost."""
    ops = [
        _buy("PKN", datetime(2024, 1, 1), 20, 50.0),
        _sell("PKN", datetime(2024, 6, 1), 10, 60.0),   # +100, out of scope
        _sell("PKN", datetime(2025, 6, 1), 10, 70.0),   # +200, in scope
    ]
    result = compute_realized_pnl(ops, year=2025)
    entry = result["by_ticker"][0]
    assert entry["sales"] == 1
    assert entry["cost"] == pytest.approx(500.0), "the 2024 sale must still consume its lot"
    assert entry["pnl"] == pytest.approx(200.0)
    # Both years remain offerable even though only one was asked for.
    assert result["all_years"] == [2025, 2024]


def test_results_are_ordered_best_first_and_totals_add_up():
    ops = [
        _buy("A", datetime(2025, 1, 1), 10, 10.0),
        _sell("A", datetime(2025, 2, 1), 10, 5.0),    # -50
        _buy("B", datetime(2025, 1, 1), 10, 10.0),
        _sell("B", datetime(2025, 2, 1), 10, 20.0),   # +100
    ]
    result = compute_realized_pnl(ops)
    assert [e["ticker"] for e in result["by_ticker"]] == ["B", "A"]
    assert result["total_pnl"] == pytest.approx(50.0)
    assert result["total_proceeds"] == pytest.approx(250.0)
    assert result["total_cost"] == pytest.approx(200.0)


def test_no_trades_at_all_still_returns_a_usable_shape():
    """The tab renders this before any import — every key has to be present."""
    result = compute_realized_pnl([])
    assert result == {
        "total_pnl": 0, "total_proceeds": 0, "total_cost": 0,
        "by_ticker": [], "all_years": [], "unmatched_tickers": [],
    }
