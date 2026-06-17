import json
from unittest.mock import MagicMock, patch

import pytest

from src.exceptions import AnalysisError
from src.gemini_client import PortfolioExtraction, extract_portfolio_snapshot


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    resp = MagicMock()
    resp.text = response_text
    client.models.generate_content.return_value = resp
    return client


def _write_fake_screenshot(tmp_path, name: str = "shot.png") -> str:
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n fake bytes for test, not a real image")
    return str(path)


# ── portfolio extraction (PUL-39) ──────────────────────────────────────────────

def test_extract_returns_confident_snapshot_with_empty_uncertain_fields(tmp_path):
    payload = json.dumps({
        "total_value": 12345.67,
        "currency": "PLN",
        "positions": [
            {"ticker": "PKO", "value": 5000.0, "pct": 40.5},
            {"ticker": "XTB", "value": 7345.67, "pct": 59.5},
        ],
        "uncertain_fields": [],
    })
    image_path = _write_fake_screenshot(tmp_path)
    with patch("src.gemini_client.get_client", return_value=_mock_client(payload)):
        result = extract_portfolio_snapshot([image_path])

    assert isinstance(result, PortfolioExtraction)
    assert result.total_value == 12345.67
    assert result.currency == "PLN"
    assert [p.ticker for p in result.positions] == ["PKO", "XTB"]
    assert result.uncertain_fields == []


def test_extract_flags_uncertain_fields(tmp_path):
    payload = json.dumps({
        "total_value": 999.0,
        "currency": "PLN",
        "positions": [],
        "uncertain_fields": ["total_value", "currency"],
    })
    image_path = _write_fake_screenshot(tmp_path)
    with patch("src.gemini_client.get_client", return_value=_mock_client(payload)):
        result = extract_portfolio_snapshot([image_path])

    assert result.uncertain_fields == ["total_value", "currency"]


def test_extract_tolerates_trailing_comma_json(tmp_path):
    # Gemini Flash returns malformed JSON (trailing commas) ~14% of the time —
    # per context/foundation lessons, this must go through json5, not json.loads.
    payload = (
        '{"total_value": 100.0, "currency": "PLN", "positions": [], '
        '"uncertain_fields": [],}'
    )
    image_path = _write_fake_screenshot(tmp_path)
    with patch("src.gemini_client.get_client", return_value=_mock_client(payload)):
        result = extract_portfolio_snapshot([image_path])

    assert result.total_value == 100.0


def test_extract_tolerates_missing_position_pct(tmp_path):
    # Real XTB screenshots don't always show a per-position percentage allocation
    # (PUL-39 manual round-trip finding) — pct must be optional, not a hard crash.
    payload = json.dumps({
        "total_value": 1000.0,
        "currency": "PLN",
        "positions": [{"ticker": "PKO", "value": 1000.0, "pct": None}],
        "uncertain_fields": ["PKO.pct"],
    })
    image_path = _write_fake_screenshot(tmp_path)
    with patch("src.gemini_client.get_client", return_value=_mock_client(payload)):
        result = extract_portfolio_snapshot([image_path])

    assert result.positions[0].pct is None
    assert result.uncertain_fields == ["PKO.pct"]


def test_extract_falls_back_to_last_item_when_response_is_a_list(tmp_path):
    # Despite the prompt instructing exactly one JSON object, Gemini sometimes still
    # returns one object per input image for multi-screenshot wallets (PUL-39 manual
    # round-trip finding) — must not crash, should use the last (most complete) item.
    payload = json.dumps([
        {
            "total_value": 100.0,
            "currency": "PLN",
            "positions": [{"ticker": "PKO", "value": 100.0, "pct": None}],
            "uncertain_fields": ["PKO.pct"],
        },
        {
            "total_value": 200.0,
            "currency": "PLN",
            "positions": [
                {"ticker": "PKO", "value": 100.0, "pct": None},
                {"ticker": "XTB", "value": 100.0, "pct": None},
            ],
            "uncertain_fields": ["PKO.pct", "XTB.pct"],
        },
    ])
    image_path = _write_fake_screenshot(tmp_path)
    with patch("src.gemini_client.get_client", return_value=_mock_client(payload)):
        result = extract_portfolio_snapshot([image_path])

    assert result.total_value == 200.0
    assert [p.ticker for p in result.positions] == ["PKO", "XTB"]


def test_extract_raises_analysis_error_on_invalid_response(tmp_path):
    payload = json.dumps({"currency": "PLN"})  # missing required total_value
    image_path = _write_fake_screenshot(tmp_path)
    with patch("src.gemini_client.get_client", return_value=_mock_client(payload)):
        with pytest.raises(AnalysisError):
            extract_portfolio_snapshot([image_path])
