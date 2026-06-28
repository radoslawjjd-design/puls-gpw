import json

import pytest

from src.portfolio_treemap import compute_treemap_positions, compute_user_portfolio_treemap_positions


def _positions_json(positions: list[dict]) -> str:
    return json.dumps({"positions": positions, "media_attached": False})


def test_matched_ticker_computes_positive_delta():
    today = _positions_json([{"ticker": "PKO", "value": 1100.0, "pct": 10.0}])
    yesterday = _positions_json([{"ticker": "PKO", "value": 1000.0, "pct": 9.0}])

    result = compute_treemap_positions(today, yesterday, total_value=2200.0)

    assert result == [
        pytest.approx({
            "ticker": "PKO",
            "position_value_pln": 1100.0,
            "daily_change_pln": 100.0,
            "daily_change_pct": 10.0,
            "portfolio_share_pct": 50.0,
            "since_purchase_pct": 10.0,
            "since_purchase_pln": 100.0,
        })
    ]


def test_matched_ticker_computes_negative_and_zero_delta():
    today = _positions_json([
        {"ticker": "PKO", "value": 900.0, "pct": -10.0},
        {"ticker": "CDR", "value": 500.0, "pct": 0.0},
    ])
    yesterday = _positions_json([
        {"ticker": "PKO", "value": 1000.0, "pct": -9.0},
        {"ticker": "CDR", "value": 500.0, "pct": 0.0},
    ])

    result = compute_treemap_positions(today, yesterday, total_value=1400.0)

    assert result[0]["daily_change_pln"] == -100.0
    assert result[0]["daily_change_pct"] == -10.0
    assert result[1]["daily_change_pln"] == 0.0
    assert result[1]["daily_change_pct"] == 0.0


def test_new_ticker_not_in_yesterday_has_none_deltas():
    today = _positions_json([{"ticker": "XTB", "value": 200.0, "pct": 1.0}])
    yesterday = _positions_json([{"ticker": "PKO", "value": 1000.0, "pct": 9.0}])

    result = compute_treemap_positions(today, yesterday, total_value=200.0)

    assert len(result) == 1
    assert result[0] == pytest.approx({
        "ticker": "XTB",
        "position_value_pln": 200.0,
        "daily_change_pln": None,
        "daily_change_pct": None,
        "portfolio_share_pct": 100.0,
        "since_purchase_pct": 1.0,
        "since_purchase_pln": 200.0 - 200.0 / 1.01,
    })


def test_no_yesterday_snapshot_gives_none_deltas_for_all():
    today = _positions_json([{"ticker": "PKO", "value": 1000.0, "pct": 9.0}])

    result = compute_treemap_positions(today, None, total_value=1000.0)

    assert result[0]["daily_change_pln"] is None
    assert result[0]["daily_change_pct"] is None


def test_zero_yesterday_value_avoids_division_by_zero():
    today = _positions_json([{"ticker": "PKO", "value": 100.0, "pct": 100.0}])
    yesterday = _positions_json([{"ticker": "PKO", "value": 0.0, "pct": 0.0}])

    result = compute_treemap_positions(today, yesterday, total_value=100.0)

    assert result[0]["daily_change_pln"] == 100.0
    assert result[0]["daily_change_pct"] is None


def test_malformed_json_returns_empty_list():
    assert compute_treemap_positions("{not json", None, total_value=100.0) == []
    assert compute_treemap_positions("{}", None, total_value=100.0) == []


def test_malformed_item_in_today_positions_is_skipped_not_raised():
    today = json.dumps({"positions": [
        {"ticker": "PKO", "value": 1100.0, "pct": 10.0},
        {"ticker": "BROKEN"},  # missing "value" — must be skipped, not crash
        "not-a-dict",  # must be skipped, not crash
    ], "media_attached": False})
    yesterday = _positions_json([{"ticker": "PKO", "value": 1000.0, "pct": 9.0}])

    result = compute_treemap_positions(today, yesterday, total_value=1100.0)

    assert result == [
        pytest.approx({
            "ticker": "PKO",
            "position_value_pln": 1100.0,
            "daily_change_pln": 100.0,
            "daily_change_pct": 10.0,
            "portfolio_share_pct": 100.0,
            "since_purchase_pct": 10.0,
            "since_purchase_pln": 100.0,
        })
    ]


def test_malformed_item_in_yesterday_positions_is_ignored_not_raised():
    today = _positions_json([{"ticker": "PKO", "value": 1100.0, "pct": 10.0}])
    yesterday = json.dumps({"positions": [
        {"ticker": "PKO", "value": 1000.0, "pct": 9.0},
        {"ticker": "BROKEN"},  # missing "value" — must be ignored, not crash
        "not-a-dict",  # must be ignored, not crash
    ], "media_attached": False})

    result = compute_treemap_positions(today, yesterday, total_value=1100.0)

    assert result[0]["daily_change_pln"] == 100.0
    assert result[0]["daily_change_pct"] == 10.0


def test_portfolio_share_pct_computed_for_normal_total_value():
    today = _positions_json([
        {"ticker": "PKO", "value": 1100.0, "pct": 10.0},
        {"ticker": "CDR", "value": 900.0, "pct": 5.0},
    ])

    result = compute_treemap_positions(today, None, total_value=2000.0)

    assert result[0]["portfolio_share_pct"] == pytest.approx(55.0)
    assert result[1]["portfolio_share_pct"] == pytest.approx(45.0)


def test_zero_total_value_gives_none_share_pct_for_every_position():
    today = _positions_json([
        {"ticker": "PKO", "value": 1100.0, "pct": 10.0},
        {"ticker": "CDR", "value": 900.0, "pct": 5.0},
    ])

    result = compute_treemap_positions(today, None, total_value=0.0)

    assert result[0]["portfolio_share_pct"] is None
    assert result[1]["portfolio_share_pct"] is None
    # existing delta fields unaffected by the total_value guard
    assert result[0]["daily_change_pln"] is None


def test_missing_pct_gives_none_since_purchase_fields():
    today = _positions_json([{"ticker": "PKO", "value": 1000.0}])

    result = compute_treemap_positions(today, None, total_value=1000.0)

    assert result[0]["since_purchase_pct"] is None
    assert result[0]["since_purchase_pln"] is None


def test_total_loss_pct_avoids_since_purchase_division_by_zero():
    today = _positions_json([{"ticker": "PKO", "value": 0.0, "pct": -100.0}])

    result = compute_treemap_positions(today, None, total_value=0.0)

    assert result[0]["since_purchase_pct"] is None
    assert result[0]["since_purchase_pln"] is None


def test_since_purchase_pnl_computed_for_normal_positive_pct():
    today = _positions_json([{"ticker": "PKO", "value": 1200.0, "pct": 20.0}])

    result = compute_treemap_positions(today, None, total_value=1200.0)

    assert result[0]["since_purchase_pct"] == 20.0
    assert result[0]["since_purchase_pln"] == pytest.approx(200.0)


# ---------------------------------------------------------------------------
# compute_user_portfolio_treemap_positions — user-positions compute function
# ---------------------------------------------------------------------------


def _row(ticker="PKO", company_name="PKO BP", shares=100.0, avg_buy_price=45.0,
         current_price=50.0, daily_change_pct=2.0):
    return {
        "ticker": ticker, "company_name": company_name, "shares": shares,
        "avg_buy_price": avg_buy_price, "current_price": current_price,
        "daily_change_pct": daily_change_pct,
    }


def test_user_compute_full_price_data_all_fields_correct():
    result = compute_user_portfolio_treemap_positions([_row()])

    assert len(result) == 1
    pos = result[0]
    assert pos["ticker"] == "PKO"
    assert pos["position_value_pln"] == pytest.approx(5000.0)   # 100 * 50
    assert pos["daily_change_pct"] == pytest.approx(2.0)
    # daily_change_pln = 5000 * 2 / 100 / (1 + 2/100) = 100 / 1.02 ≈ 98.04
    assert pos["daily_change_pln"] == pytest.approx(5000.0 * 2 / 100 / (1 + 2 / 100))
    assert pos["since_purchase_pct"] == pytest.approx((50.0 / 45.0 - 1) * 100)
    assert pos["since_purchase_pln"] == pytest.approx((50.0 - 45.0) * 100)
    assert pos["portfolio_share_pct"] == pytest.approx(100.0)


def test_user_compute_no_price_all_money_fields_none():
    result = compute_user_portfolio_treemap_positions([_row(current_price=None, daily_change_pct=None)])

    assert len(result) == 1
    pos = result[0]
    assert pos["position_value_pln"] is None
    assert pos["daily_change_pln"] is None
    assert pos["daily_change_pct"] is None
    assert pos["since_purchase_pct"] is None
    assert pos["since_purchase_pln"] is None
    assert pos["portfolio_share_pct"] is None


def test_user_compute_empty_input_returns_empty():
    assert compute_user_portfolio_treemap_positions([]) == []


def test_user_compute_multiple_positions_share_pct_sums_to_100():
    rows = [
        _row("PKO", shares=100.0, avg_buy_price=40.0, current_price=50.0),
        _row("CDR", shares=10.0, avg_buy_price=100.0, current_price=200.0),
    ]
    result = compute_user_portfolio_treemap_positions(rows)

    total = sum(r["portfolio_share_pct"] for r in result)
    assert total == pytest.approx(100.0)


def test_user_compute_zero_avg_buy_price_since_purchase_pct_none():
    result = compute_user_portfolio_treemap_positions([_row(avg_buy_price=0.0)])

    assert result[0]["since_purchase_pct"] is None


def test_user_compute_no_price_position_excluded_from_share_pct_denominator():
    rows = [
        _row("PKO", shares=100.0, avg_buy_price=40.0, current_price=50.0),   # value=5000
        _row("CDR", shares=10.0, avg_buy_price=100.0, current_price=None),   # no price
    ]
    result = compute_user_portfolio_treemap_positions(rows)

    pko = next(r for r in result if r["ticker"] == "PKO")
    cdr = next(r for r in result if r["ticker"] == "CDR")
    assert pko["portfolio_share_pct"] == pytest.approx(100.0)  # only PKO in denominator
    assert cdr["portfolio_share_pct"] is None


def test_user_compute_daily_change_pct_minus_100_does_not_raise():
    rows = [_row(current_price=0.01, daily_change_pct=-100.0)]
    result = compute_user_portfolio_treemap_positions(rows)

    assert len(result) == 1
    assert result[0]["daily_change_pln"] is None  # denom == 0 → None, not ZeroDivisionError
