import json
from unittest.mock import MagicMock, patch

from src.post_generator import (
    GeneratedPost,
    generate_post,
    _build_tickers_str,
    _normalize_ticker_spacing,
    _enforce_body_cashtag,
    _enforce_body_ticker_ref,
    _strip_domain_suffix,
    _enforce_length,
    _HOOK_VARIANTS,
)
from src.post_supervisor import validate_post

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


def test_a_body_tweet_missing_its_paren_ticker_is_repaired_end_to_end():
    """The helper passing in isolation proves nothing about the pipeline — the repair
    has to be wired into generate_post's per-tweet loop, and it has to run before
    _enforce_length so the added characters are inside the trim's budget.

    This is the 2026-07-30 failure in miniature: Gemini names the company, omits the
    parenthesised ticker, and the supervisor throws the whole thread away.
    """
    tweets = [
        "🚨 2 kluczowe ESPI z GPW dzisiaj:",
        "📊 PKO\nWyniki Q1: 120,1 mln PLN zysku netto.",
        "📊 XTB ( $XTB )\nWzrost klientów o 27%.",
        "Która spółka Cię interesuje? #GPW Nie jest to rekomendacja inwestycyjna.",
    ]
    payload = json.dumps({"tweets": tweets}, ensure_ascii=False)
    with patch("src.post_generator.get_client", return_value=_mock_client(payload)):
        result = generate_post(_ANNOUNCEMENTS)

    assert "( $PKO )" in result.tweets[1]
    assert validate_post(result, ["XTB", "PKO"], expected_tweets=4).approved


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


# ── ticker spacing in parens ─────────────────────────────────────────────────

def test_normalize_ticker_spacing_adds_spaces():
    assert _normalize_ticker_spacing("📊 Lubawa (LBW)") == "📊 Lubawa ( LBW )"
    assert _normalize_ticker_spacing("• Ekobox ($EBX) umowa") == "• Ekobox ( $EBX ) umowa"


def test_normalize_ticker_spacing_idempotent():
    assert _normalize_ticker_spacing("Lubawa ( $LBW )") == "Lubawa ( $LBW )"


def test_normalize_ticker_spacing_leaves_year_alone():
    # Pure-number parens (e.g. a year) are not tickers — must stay untouched.
    assert _normalize_ticker_spacing("zysk za rok (2025) rośnie") == "zysk za rok (2025) rośnie"


# ── body-tweet cashtag enforcement ───────────────────────────────────────────

def test_enforce_body_cashtag_adds_dollar_to_single_company_tweet():
    tweet = "📊 Synektik SA ( SNT )\nPrzychody: 100 mln PLN\nRekord?"
    assert _enforce_body_cashtag(tweet) == "📊 Synektik SA ( $SNT )\nPrzychody: 100 mln PLN\nRekord?"


def test_enforce_body_cashtag_idempotent_when_already_tagged():
    tweet = "📊 Apator SA ( $APT )\nZysk: 1 mln\nMocno."
    assert _enforce_body_cashtag(tweet) == tweet


def test_enforce_body_cashtag_noop_on_multiple_tickers():
    # Several distinct tickers (e.g. a closing line) → ambiguous, leave untouched.
    tweet = "( PKO ), ( XTB ) czy ( LBW )?"
    assert _enforce_body_cashtag(tweet) == tweet


def test_enforce_body_ticker_ref_adds_the_paren_the_supervisor_requires():
    """The 2026-07-30 regression: Gemini named LUG but never parenthesised it, the
    supervisor rejected twice on `missing (LUG)`, and the window lost its post."""
    tweet = "📊 LUG\nUkład przyjęty przez większość wierzycieli."
    out = _enforce_body_ticker_ref(tweet, ["GTS", "LUG", "ARL"])
    assert out == "📊 LUG ( $LUG )\nUkład przyjęty przez większość wierzycieli."
    # The repaired tweet has to satisfy the supervisor's own check, not merely look right.
    assert validate_post(GeneratedPost(tweets=["hook", out, "closing"]), ["LUG"]).issues == [
        "missing #GPW in last tweet",
        "missing disclaimer ('rekomendacj') in last tweet",
    ]


def test_enforce_body_ticker_ref_is_a_noop_when_already_parenthesised():
    tweet = "📊 LUG ( $LUG )\nUkład przyjęty."
    assert _enforce_body_ticker_ref(tweet, ["LUG"]) == tweet


def test_enforce_body_ticker_ref_marks_only_the_first_mention():
    """A second `( $LUG )` in the same tweet would read as a duplicate cashtag."""
    out = _enforce_body_ticker_ref("LUG rośnie, a LUG dalej rośnie", ["LUG"])
    assert out == "LUG ( $LUG ) rośnie, a LUG dalej rośnie"


def test_enforce_body_ticker_ref_refuses_to_guess_between_two_companies():
    """Two tickers named means the tweet's subject is ambiguous. Labelling it with
    the wrong company is a factual error on a public post — worse than no post."""
    tweet = "GTS i ARL podpisały umowę"
    assert _enforce_body_ticker_ref(tweet, ["GTS", "ARL"]) == tweet


def test_enforce_body_ticker_ref_leaves_a_tweet_naming_no_ticker_alone():
    """"Geotrans" without "GTS" gives nothing to key the repair on."""
    tweet = "📊 Geotrans\nPrzychody spadły o 51%."
    assert _enforce_body_ticker_ref(tweet, ["GTS"]) == tweet


def test_enforce_body_ticker_ref_does_not_fire_inside_a_longer_word():
    """`\\b` matters: LUG inside "SLUGI" is not a ticker mention."""
    tweet = "SLUGI wzrosły"
    assert _enforce_body_ticker_ref(tweet, ["LUG"]) == tweet


def test_enforce_body_cashtag_leaves_year_alone():
    assert _enforce_body_cashtag("zysk za rok (2025) rośnie") == "zysk za rok (2025) rośnie"


# ── domain-like text guard ───────────────────────────────────────────────────

def test_strip_domain_suffix_strips_known_tlds():
    assert _strip_domain_suffix("📊 Oponeo.pl ( OPN )") == "📊 Oponeo ( OPN )"
    assert _strip_domain_suffix("Allegro.com rośnie") == "Allegro rośnie"
    assert _strip_domain_suffix("Firma.net i Firma.org i Firma.info") == "Firma i Firma i Firma"
    assert _strip_domain_suffix("Firma.io oraz Firma.co") == "Firma oraz Firma"


def test_strip_domain_suffix_case_insensitive():
    assert _strip_domain_suffix("Oponeo.PL") == "Oponeo"


def test_strip_domain_suffix_leaves_non_tld_dotted_text_alone():
    # "pl" must be a whole TLD token, not a prefix of a longer word.
    assert _strip_domain_suffix("firma.placu") == "firma.placu"
    # "plus" near-miss — no TLD boundary right after the dot.
    assert _strip_domain_suffix("firma.plus") == "firma.plus"
    # Plain numeric/year-like dotted text is untouched.
    assert _strip_domain_suffix("wynik 12.5 mln PLN") == "wynik 12.5 mln PLN"


def test_strip_domain_suffix_idempotent():
    once = _strip_domain_suffix("Oponeo.pl")
    twice = _strip_domain_suffix(once)
    assert once == twice == "Oponeo"


def test_generate_post_strips_domain_like_company_name():
    raw = json.dumps({"tweets": [
        "🚨 1 ważne ESPI z GPW:\n• Oponeo.pl ( $OPN )\nKtóra?",
        "📊 Oponeo.pl ( OPN )\nZysk: 1 mln\nMocno.",
        "Co sądzisz? #GPW #ESPI #SmallCaps Nie jest to rekomendacja inwestycyjna.",
    ]}, ensure_ascii=False)
    with patch("src.post_generator.get_client", return_value=_mock_client(raw)):
        result = generate_post(_ANNOUNCEMENTS[:1])
    assert result is not None
    joined = "".join(result.tweets)
    assert "Oponeo" in joined
    assert ".pl" not in joined.lower()


def test_generate_post_body_tweets_carry_company_cashtag():
    raw = json.dumps({"tweets": [
        "🚨 2 ważne ESPI z GPW:\n• PKO Bank Polski ( $PKO )\n• XTB SA ( XTB )\nKtóra?",
        "📊 PKO Bank Polski ( PKO )\nZysk: 120 mln PLN\nDobry trend?",
        "📊 XTB SA ( XTB )\nWzrost 27%\nRekord?",
        "Co sądzisz? #GPW #ESPI Nie jest to rekomendacja inwestycyjna.",
    ]}, ensure_ascii=False)
    with patch("src.post_generator.get_client", return_value=_mock_client(raw)):
        result = generate_post(_ANNOUNCEMENTS)
    assert result is not None
    # Each per-company body tweet gets its own cashtag.
    assert "( $PKO )" in result.tweets[1]
    assert "( $XTB )" in result.tweets[2]
    # Hook keeps exactly one cashtag (top company); closing stays plain.
    assert result.tweets[0].count("$") == 1
    assert "$" not in result.tweets[3]


def test_generate_post_injects_single_cashtag_for_first_company():
    payload = json.dumps({"tweets": _SIX_TWEETS}, ensure_ascii=False)
    with patch("src.post_generator.get_client", return_value=_mock_client(payload)) as mock_get:
        generate_post(_ANNOUNCEMENTS)
    call_contents = mock_get.return_value.models.generate_content.call_args[1]["contents"]
    # Top company = first in (score-DESC) input → its cashtag is passed exactly once.
    assert 'cashtag_spolki: "$PKO"' in call_contents


def test_a_malformed_top_ticker_produces_a_thread_with_no_cashtag():
    """PUL-102: the 2026-07-30 thread went out as `$Zabka` because the stored
    ticker was a brand name and every deterministic repair here is anchored on an
    ALL-CAPS regex — correct, so that `(2025)` is left alone, and therefore blind
    to a ticker that is not one. A missing cashtag costs reach; a wrong one is a
    public error, so the prompt asks for no cashtag at all."""
    broken = [{**_ANNOUNCEMENTS[0], "ticker": "Żabka"}, *_ANNOUNCEMENTS[1:]]
    payload = json.dumps({"tweets": _SIX_TWEETS}, ensure_ascii=False)
    with patch("src.post_generator.get_client", return_value=_mock_client(payload)) as mock_get:
        generate_post(broken)

    call_contents = mock_get.return_value.models.generate_content.call_args[1]["contents"]
    assert "cashtag_spolki" not in call_contents, "a malformed ticker must not be asked for"
    assert "$Żabka" not in call_contents


def test_generate_post_normalizes_ticker_spacing_in_returned_tweets():
    raw = json.dumps({"tweets": [
        "🚨 1 ważne ESPI z GPW:\n• Lubawa ($LBW)\nKtóra?",
        "📊 Lubawa (LBW)\nZysk: 1 mln\nMocno.",
        "Co sądzisz? #GPW #ESPI #SmallCaps",
    ]}, ensure_ascii=False)
    with patch("src.post_generator.get_client", return_value=_mock_client(raw)):
        result = generate_post(_ANNOUNCEMENTS[:1])
    assert result is not None
    assert "( $LBW )" in result.tweets[0]
    # Body tweet carries the company's single cashtag (one cashtag per tweet).
    assert "( $LBW )" in result.tweets[1]
    assert "($LBW)" not in "".join(result.tweets)


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


# ── _enforce_length ──────────────────────────────────────────────────────────

def test_enforce_length_noop_when_short():
    tweet = "📊 PKO Bank Polski ( PKO )\nZysk netto: 120,1 mln PLN.\nDobry trend?"
    assert _enforce_length(tweet) == tweet


def test_enforce_length_trims_oversized_hook_with_multiple_bullets():
    filler = "opis bardzo długiego wydarzenia korporacyjnego testowego " * 6
    hook = (
        "🚨 2 ważne ESPI z GPW – sprawdź teraz:\n"
        f"• PKO Bank Polski ( $PKO ) {filler}\n"
        f"• XTB SA ( XTB ) {filler}\n"
        "Która spółka Cię interesuje?"
    )
    assert len(hook) > 280

    result = _enforce_length(hook)

    assert len(result) <= 280


def test_enforce_length_preserves_ticker_paren_in_body_tweet():
    filler = "Bardzo długi opis zdarzenia korporacyjnego testowego. " * 8
    tweet = f"📊 PKO Bank Polski ( PKO )\n{filler}Koniec."
    assert len(tweet) > 280

    result = _enforce_length(tweet)

    assert len(result) <= 280
    assert "( PKO )" in result


def test_enforce_length_preserves_hashtags_and_disclaimer_in_closing_tweet():
    long_question = "Bardzo długie testowe pytanie zamykające wątek, które z pewnością przekroczy limit znaków platformy X. " * 2
    tweet = (
        f"{long_question}Napisz w komentarzu!\n\n"
        "💾 Zapisz na później\n"
        "Nie jest to rekomendacja inwestycyjna. #GPW #ESPI #SmallCaps"
    )
    assert len(tweet) > 280

    result = _enforce_length(tweet)

    assert len(result) <= 280
    assert "#GPW" in result
    assert "rekomendacj" in result.lower()


def test_enforce_length_never_ends_in_ellipsis():
    tweet = "📊 Spółka ( ABC )\n" + "Słowo " * 60 + "podsumowanie..."
    assert len(tweet) > 280

    result = _enforce_length(tweet)

    assert len(result) <= 280
    assert not result.endswith("...")
    assert not result.endswith("…")


def test_enforce_length_idempotent_on_oversized_tweet():
    filler = "Bardzo długi opis zdarzenia korporacyjnego testowego. " * 8
    tweet = f"📊 PKO Bank Polski ( PKO )\n{filler}Koniec."

    once = _enforce_length(tweet)
    twice = _enforce_length(once)

    assert once == twice
    assert len(once) <= 280


# ── Phase 2: prompt/feedback tightening ──────────────────────────────────────

def test_system_prompt_has_hook_char_budget_guidance():
    payload = json.dumps({"tweets": _SIX_TWEETS}, ensure_ascii=False)
    with patch("src.post_generator.get_client", return_value=_mock_client(payload)) as mock_get:
        generate_post(_ANNOUNCEMENTS)
    config = mock_get.return_value.models.generate_content.call_args[1]["config"]
    assert "280 znakach łącznie" in config.system_instruction
    assert "im więcej spółek" in config.system_instruction.lower()


def test_feedback_block_names_event_description_as_trim_target():
    payload = json.dumps({"tweets": _SIX_TWEETS}, ensure_ascii=False)
    with patch("src.post_generator.get_client", return_value=_mock_client(payload)) as mock_get:
        generate_post(_ANNOUNCEMENTS, previous_issues=["tweet 1 exceeds 280 chars (310)"])
    call_contents = mock_get.return_value.models.generate_content.call_args[1]["contents"]
    assert "opis zdarzenia" in call_contents
    assert "260 znaków" in call_contents


def test_round_trip_oversized_tweets_get_trimmed_and_approved():
    filler = "opis bardzo długiego wydarzenia korporacyjnego testowego " * 6
    oversized_hook = (
        "🚨 2 ważne ESPI z GPW – sprawdź teraz:\n"
        f"• PKO Bank Polski ( $PKO ) {filler}\n"
        f"• XTB SA ( XTB ) {filler}\n"
        "Która spółka Cię interesuje?"
    )
    oversized_body = f"📊 PKO Bank Polski ( PKO )\n{filler}Dobry wynik?"
    tweets = [
        oversized_hook,
        oversized_body,
        "📊 XTB SA ( XTB )\nWzrost klientów o 27%. Najlepszy kwartał?",
        "Która spółka Cię interesuje? #GPW #ESPI Nie jest to rekomendacja inwestycyjna.",
    ]
    assert len(oversized_hook) > 280
    assert len(oversized_body) > 280

    payload = json.dumps({"tweets": tweets}, ensure_ascii=False)
    with patch("src.post_generator.get_client", return_value=_mock_client(payload)):
        result = generate_post(_ANNOUNCEMENTS)

    assert result is not None
    assert all(len(t) <= 280 for t in result.tweets)

    validation = validate_post(result, tickers=["PKO", "XTB"], expected_tweets=4)
    assert validation.approved is True
    assert not any("exceeds 280" in issue for issue in validation.issues)
    assert not any("truncated" in issue for issue in validation.issues)
