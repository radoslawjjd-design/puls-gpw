import json

from src.portfolio_treemap import compute_treemap_positions


def _positions_json(positions: list[dict]) -> str:
    return json.dumps({"positions": positions, "media_attached": False})


def test_matched_ticker_computes_positive_delta():
    today = _positions_json([{"ticker": "PKO", "value": 1100.0, "pct": 10.0}])
    yesterday = _positions_json([{"ticker": "PKO", "value": 1000.0, "pct": 9.0}])

    result = compute_treemap_positions(today, yesterday)

    assert result == [
        {
            "ticker": "PKO",
            "position_value_pln": 1100.0,
            "daily_change_pln": 100.0,
            "daily_change_pct": 10.0,
        }
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

    result = compute_treemap_positions(today, yesterday)

    assert result[0]["daily_change_pln"] == -100.0
    assert result[0]["daily_change_pct"] == -10.0
    assert result[1]["daily_change_pln"] == 0.0
    assert result[1]["daily_change_pct"] == 0.0


def test_new_ticker_not_in_yesterday_has_none_deltas():
    today = _positions_json([{"ticker": "XTB", "value": 200.0, "pct": 1.0}])
    yesterday = _positions_json([{"ticker": "PKO", "value": 1000.0, "pct": 9.0}])

    result = compute_treemap_positions(today, yesterday)

    assert result == [
        {
            "ticker": "XTB",
            "position_value_pln": 200.0,
            "daily_change_pln": None,
            "daily_change_pct": None,
        }
    ]


def test_no_yesterday_snapshot_gives_none_deltas_for_all():
    today = _positions_json([{"ticker": "PKO", "value": 1000.0, "pct": 9.0}])

    result = compute_treemap_positions(today, None)

    assert result[0]["daily_change_pln"] is None
    assert result[0]["daily_change_pct"] is None


def test_zero_yesterday_value_avoids_division_by_zero():
    today = _positions_json([{"ticker": "PKO", "value": 100.0, "pct": 100.0}])
    yesterday = _positions_json([{"ticker": "PKO", "value": 0.0, "pct": 0.0}])

    result = compute_treemap_positions(today, yesterday)

    assert result[0]["daily_change_pln"] == 100.0
    assert result[0]["daily_change_pct"] is None


def test_malformed_json_returns_empty_list():
    assert compute_treemap_positions("{not json", None) == []
    assert compute_treemap_positions("{}", None) == []


def test_malformed_item_in_today_positions_is_skipped_not_raised():
    today = json.dumps({"positions": [
        {"ticker": "PKO", "value": 1100.0, "pct": 10.0},
        {"ticker": "BROKEN"},  # missing "value" — must be skipped, not crash
        "not-a-dict",  # must be skipped, not crash
    ], "media_attached": False})
    yesterday = _positions_json([{"ticker": "PKO", "value": 1000.0, "pct": 9.0}])

    result = compute_treemap_positions(today, yesterday)

    assert result == [
        {
            "ticker": "PKO",
            "position_value_pln": 1100.0,
            "daily_change_pln": 100.0,
            "daily_change_pct": 10.0,
        }
    ]


def test_malformed_item_in_yesterday_positions_is_ignored_not_raised():
    today = _positions_json([{"ticker": "PKO", "value": 1100.0, "pct": 10.0}])
    yesterday = json.dumps({"positions": [
        {"ticker": "PKO", "value": 1000.0, "pct": 9.0},
        {"ticker": "BROKEN"},  # missing "value" — must be ignored, not crash
        "not-a-dict",  # must be ignored, not crash
    ], "media_attached": False})

    result = compute_treemap_positions(today, yesterday)

    assert result[0]["daily_change_pln"] == 100.0
    assert result[0]["daily_change_pct"] == 10.0
