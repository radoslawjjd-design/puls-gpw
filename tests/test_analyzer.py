import json
import logging
from unittest.mock import MagicMock, patch

from src.analyzer import AnalysisResult, _compute_score, analyze_announcement

_ANN_ID = "test-ann-001"
_PARSED_CONTENT = (
    "PKO BP wykazała przychody 120 100 000 PLN za Q1 2026. "
    "Zysk netto wyniósł 45 000 000 PLN."
)

_ANALYSIS_DICT = {
    "company": "PKO Bank Polski",
    "ticker": "PKO",
    "event_type": "wyniki_finansowe",
    "key_numbers": ["120,1 mln PLN", "45 mln PLN"],
    "sentiment": "positive",
    "summary_pl": "Dobry wynik Q1 2026.",
}

_GATE_APPROVED = {"approved": True, "reason": None}
_GATE_REJECTED = {"approved": False, "reason": "liczby niezgodne ze źródłem"}


def _mock_client(*response_dicts: dict) -> MagicMock:
    client = MagicMock()
    mocks = []
    for d in response_dicts:
        resp = MagicMock()
        resp.text = json.dumps(d)
        mocks.append(resp)
    client.models.generate_content.side_effect = mocks
    return client


# ── Skip conditions ──────────────────────────────────────────────────────────

def test_skip_no_parsed_content():
    result = analyze_announcement(_ANN_ID, None, None, None)
    assert result == AnalysisResult(
        announcement_id=_ANN_ID,
        structured_analysis=None,
        analysis_approved=None,
        analysis_reject_reason=None,
        event_type=None,
        analysis_score=None,
    )


def test_skip_empty_parsed_content():
    result = analyze_announcement(_ANN_ID, "", None, None)
    assert result.structured_analysis is None
    assert result.analysis_approved is None
    assert result.event_type is None
    assert result.analysis_score is None


# ── Error handling ───────────────────────────────────────────────────────────

def test_gemini_api_error_analysis(caplog):
    with caplog.at_level(logging.WARNING, logger="src.analyzer"):
        with patch("src.analyzer._get_client") as mock_get:
            client = MagicMock()
            client.models.generate_content.side_effect = Exception("API error")
            mock_get.return_value = client
            result = analyze_announcement(_ANN_ID, _PARSED_CONTENT, None, None)

    assert result.structured_analysis is None
    assert result.analysis_score is None
    assert "analysis call failed" in caplog.text


def test_gemini_api_error_gate():
    analysis_resp = MagicMock()
    analysis_resp.text = json.dumps(_ANALYSIS_DICT)

    with patch("src.analyzer._get_client") as mock_get:
        client = MagicMock()
        client.models.generate_content.side_effect = [analysis_resp, Exception("gate error")]
        mock_get.return_value = client
        result = analyze_announcement(_ANN_ID, _PARSED_CONTENT, "PKO", None)

    assert result.structured_analysis is not None
    assert result.event_type == "wyniki_finansowe"
    assert result.analysis_approved is None
    assert result.analysis_score is None


# ── Happy paths ──────────────────────────────────────────────────────────────

def test_happy_path_approved():
    with patch("src.analyzer._get_client") as mock_get:
        mock_get.return_value = _mock_client(_ANALYSIS_DICT, _GATE_APPROVED)
        result = analyze_announcement(_ANN_ID, _PARSED_CONTENT, "PKO", None)

    assert result.analysis_approved is True
    assert result.analysis_score is not None
    assert result.analysis_score > 0
    assert result.event_type == "wyniki_finansowe"


def test_happy_path_rejected():
    with patch("src.analyzer._get_client") as mock_get:
        mock_get.return_value = _mock_client(_ANALYSIS_DICT, _GATE_REJECTED)
        result = analyze_announcement(_ANN_ID, _PARSED_CONTENT, "PKO", None)

    assert result.analysis_approved is False
    assert result.analysis_score is None
    assert result.analysis_reject_reason == "liczby niezgodne ze źródłem"


# ── Event type mapping ───────────────────────────────────────────────────────

def test_unknown_event_type_maps_to_inne():
    unknown_dict = {**_ANALYSIS_DICT, "event_type": "nieznany_typ"}
    with patch("src.analyzer._get_client") as mock_get:
        mock_get.return_value = _mock_client(unknown_dict, _GATE_APPROVED)
        result = analyze_announcement(_ANN_ID, _PARSED_CONTENT, None, None)

    assert result.event_type == "inne"


# ── _compute_score ───────────────────────────────────────────────────────────

def test_compute_score_tier1():
    # XTB is Tier 1 (+40); "inne" = 20; no priority
    assert _compute_score("inne", "XTB", None) == 60.0


def test_compute_score_tier2():
    # PKO is Tier 2 (+25); "inne" = 20; no priority
    assert _compute_score("inne", "PKO", None) == 45.0


def test_compute_score_tier4_unknown():
    # Unknown ticker → tier_bonus = 0; "inne" = 20; no priority
    assert _compute_score("inne", "ZZZ", None) == 20.0


def test_compute_score_priority_bonus():
    # "Ważny" adds +20; no tier; "inne" = 20
    assert _compute_score("inne", None, "Ważny") == 40.0


def test_compute_score_no_priority():
    # priority=None → priority_bonus = 0; no tier; "inne" = 20
    assert _compute_score("inne", None, None) == 20.0


def test_compute_score_wyniki_finansowe():
    # event_score = 100; no tier; no priority
    assert _compute_score("wyniki_finansowe", None, None) == 100.0


def test_compute_score_upadlosc():
    # event_score = 95; no tier; no priority
    assert _compute_score("upadlosc", None, None) == 95.0


# ── Trailing comma regression (json5) ────────────────────────────────────────

def test_trailing_comma_json_handled():
    analysis_resp = MagicMock()
    analysis_resp.text = (
        '{"company": "PKO Bank Polski", "ticker": "PKO",'
        ' "event_type": "wyniki_finansowe",'
        ' "key_numbers": ["120,1 mln PLN",],'
        ' "sentiment": "positive",'
        ' "summary_pl": "Dobry wynik Q1 2026.",}'
    )
    gate_resp = MagicMock()
    gate_resp.text = '{"approved": true, "reason": null,}'

    with patch("src.analyzer._get_client") as mock_get:
        client = MagicMock()
        client.models.generate_content.side_effect = [analysis_resp, gate_resp]
        mock_get.return_value = client
        result = analyze_announcement(_ANN_ID, _PARSED_CONTENT, "PKO", None)

    assert result.structured_analysis is not None
    assert result.analysis_approved is True
