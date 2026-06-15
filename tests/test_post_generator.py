import json
from unittest.mock import MagicMock, patch

from src.post_generator import (
    GeneratedPost,
    generate_post,
    _build_tickers_str,
    _to_bold,
    _apply_bold,
    _HOOK_VARIANTS,
    _CLOSING_QUESTIONS,
)

_ANNOUNCEMENTS = [
    {
        "announcement_id": "id1",
        "ticker": "PKO",
        "company": "PKO Bank Polski",
        "event_type": "wyniki_finansowe",
        "structured_analysis": json.dumps({
            "key_numbers": ["120,1 mln PLN"],
            "summary_pl": "Dobry wynik Q1 2026.",
        }),
        "analysis_score": 125.0,
        "url": "http://example.com/1",
    },
    {
        "announcement_id": "id2",
        "ticker": "XTB",
        "company": "XTB SA",
        "event_type": "wyniki_finansowe",
        "structured_analysis": json.dumps({
            "key_numbers": ["27%"],
            "summary_pl": "Wzrost klientów.",
        }),
        "analysis_score": 140.0,
        "url": "http://example.com/2",
    },
]

_SIX_TWEETS = [
    "🚨 4 kluczowe ESPI z GPW dzisiaj:",
    "$PKO – Wyniki Q1: 120,1 mln PLN zysku netto. Dobry trend?",
    "$XTB – Wzrost klientów o 27%. Najlepszy kwartał?",
    "$PZU – Składka +18% r/r. Rekord?",
    "$CDR – Nowa platforma z AI. Wzrost potencjalny?",
    "Która spółka Cię interesuje? 👇 #GPW #ESPI Nie jest to rekomendacja inwestycyjna.",
]


def _mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    resp = MagicMock()
    resp.text = response_text
    client.models.generate_content.return_value = resp
    return client


# ── Happy path ────────────────────────────────────────────────────────────────

def test_happy_path_returns_generated_post():
    payload = json.dumps({"tweets": _SIX_TWEETS}, ensure_ascii=False)
    with patch("src.post_generator.get_client", return_value=_mock_client(payload)):
        result = generate_post(_ANNOUNCEMENTS)

    assert isinstance(result, GeneratedPost)
    assert len(result.tweets) == 6
    assert result.tweets[0] == _SIX_TWEETS[0]


# ── Failure paths ─────────────────────────────────────────────────────────────

def test_gemini_exception_returns_none():
    client = MagicMock()
    client.models.generate_content.side_effect = Exception("API down")
    with patch("src.post_generator.get_client", return_value=client):
        result = generate_post(_ANNOUNCEMENTS)

    assert result is None


def test_missing_tweets_key_returns_none():
    payload = json.dumps({"other_key": "value"})
    with patch("src.post_generator.get_client", return_value=_mock_client(payload)):
        result = generate_post(_ANNOUNCEMENTS)

    assert result is None


def test_empty_tweets_list_returns_none():
    payload = json.dumps({"tweets": []})
    with patch("src.post_generator.get_client", return_value=_mock_client(payload)):
        result = generate_post(_ANNOUNCEMENTS)

    assert result is None


# ── json5 trailing comma ──────────────────────────────────────────────────────

def test_trailing_comma_json_still_parses():
    payload = '{"tweets": ["tweet1", "tweet2", "tweet3", "tweet4", "tweet5", "tweet6",]}'
    with patch("src.post_generator.get_client", return_value=_mock_client(payload)):
        result = generate_post(_ANNOUNCEMENTS)

    assert isinstance(result, GeneratedPost)
    assert len(result.tweets) == 6


# ── window hook phrase ───────────────────────────────────────────────────────

def test_window_hook_phrase_injected():
    payload = json.dumps({"tweets": _SIX_TWEETS}, ensure_ascii=False)
    cases = [
        ("ranek",    "ranek"),
        ("poludnie", "poludnie"),
        ("wieczor",  "wieczor"),
        (None,       "ranek"),   # default falls back to ranek
    ]
    for window, expected_key in cases:
        with patch("src.post_generator.get_client", return_value=_mock_client(payload)) as mock_get:
            generate_post(_ANNOUNCEMENTS, window=window)
        call_contents = mock_get.return_value.models.generate_content.call_args[1]["contents"]
        assert any(
            phrase in call_contents for phrase in _HOOK_VARIANTS[expected_key]
        ), f"window={window!r}: no variant from {expected_key!r} pool found in contents"


def test_closing_question_injected():
    payload = json.dumps({"tweets": _SIX_TWEETS}, ensure_ascii=False)
    with patch("src.post_generator.get_client", return_value=_mock_client(payload)) as mock_get:
        generate_post(_ANNOUNCEMENTS)
    call_contents = mock_get.return_value.models.generate_content.call_args[1]["contents"]
    assert "fraza_closing:" in call_contents
    assert "cashtag_spolki:" in call_contents
    assert "PKO" in call_contents and "XTB" in call_contents


def test_no_valid_announcements_returns_none():
    no_ticker = [{**_ANNOUNCEMENTS[0], "ticker": None}]
    result = generate_post(no_ticker)
    assert result is None


# ── _build_tickers_str ───────────────────────────────────────────────────────

def test_build_tickers_str_single():
    assert _build_tickers_str(["PKO"]) == "PKO"


def test_build_tickers_str_two():
    assert _build_tickers_str(["PKO", "XTB"]) == "PKO czy XTB"


def test_build_tickers_str_three():
    assert _build_tickers_str(["PKO", "XTB", "LBW"]) == "PKO, XTB czy LBW"


# ── bold rendering ───────────────────────────────────────────────────────────

def _bold_cap(ch: str) -> str:
    return chr(0x1D5D4 + (ord(ch) - ord("A")))


def _bold_small(ch: str) -> str:
    return chr(0x1D5EE + (ord(ch) - ord("a")))


def test_to_bold_ascii():
    assert _to_bold("Lubawa") == (
        _bold_cap("L") + _bold_small("u") + _bold_small("b")
        + _bold_small("a") + _bold_small("w") + _bold_small("a")
    )
    assert _to_bold("7") == chr(0x1D7EC + 7)
    assert len(_to_bold("Hub.Tech")) == len("Hub.Tech")  # 1:1, "." untouched


def test_to_bold_passes_polish_diacritics_through():
    # ż has no Mathematical-Bold variant — must pass through unchanged.
    out = _to_bold("Energomontaż")
    assert out.endswith("ż")
    assert _bold_small("a") in out  # the ASCII 'a' got bolded


def test_apply_bold_only_marked_spans():
    out = _apply_bold("📊 **Lubawa** (LBW)")
    assert "**" not in out
    assert "(LBW)" in out                 # ticker untouched
    assert "📊 " in out
    assert _bold_cap("L") in out          # 'L' bolded inside the marked span


def test_apply_bold_noop_without_markers():
    assert _apply_bold("plain text (LBW)") == "plain text (LBW)"


def test_generate_post_injects_single_cashtag_for_first_company():
    payload = json.dumps({"tweets": _SIX_TWEETS}, ensure_ascii=False)
    with patch("src.post_generator.get_client", return_value=_mock_client(payload)) as mock_get:
        generate_post(_ANNOUNCEMENTS)
    call_contents = mock_get.return_value.models.generate_content.call_args[1]["contents"]
    # Top company = first in (score-DESC) input → its cashtag is passed exactly once.
    assert 'cashtag_spolki: "$PKO"' in call_contents


def test_generate_post_applies_bold_to_returned_tweets():
    bolded = json.dumps({"tweets": [
        "🚨 1 ważne ESPI z GPW:\n• **Lubawa** ( $LBW )\nKtóra?",
        "📊 **Lubawa** (LBW)\nZysk: 1 mln\nMocno.",
        "Co sądzisz? #GPW #ESPI #SmallCaps",
    ]}, ensure_ascii=False)
    with patch("src.post_generator.get_client", return_value=_mock_client(bolded)):
        result = generate_post(_ANNOUNCEMENTS[:1])
    assert result is not None
    assert all("**" not in t for t in result.tweets)
    assert _bold_cap("L") in result.tweets[0]  # bold 'L' present


# ── structured_analysis parse failure ────────────────────────────────────────

def test_bad_structured_analysis_still_calls_gemini():
    announcements_bad = [
        {**_ANNOUNCEMENTS[0], "structured_analysis": "NOT_JSON{{{"},
        _ANNOUNCEMENTS[1],
    ]
    payload = json.dumps({"tweets": _SIX_TWEETS}, ensure_ascii=False)
    with patch("src.post_generator.get_client", return_value=_mock_client(payload)):
        result = generate_post(announcements_bad)

    assert isinstance(result, GeneratedPost)
