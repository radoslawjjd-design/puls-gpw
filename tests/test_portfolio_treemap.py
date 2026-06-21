import json

import pytest

from src.portfolio_treemap import compute_treemap_positions


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
