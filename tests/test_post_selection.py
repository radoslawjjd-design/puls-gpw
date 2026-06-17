"""Unit tests for the pure selection helper (PUL-40)."""
from src.post_selection import NUMBER_DEPENDENT_EVENT_TYPES, select_top_companies


def _row(ticker, event_type, key_numbers=None, score=100.0, ann_id=None):
    if key_numbers is None:
        structured = '{"summary_pl": "qualitative"}'
    else:
        import json

        structured = json.dumps({"key_numbers": key_numbers, "summary_pl": "x"})
    return {
        "announcement_id": ann_id or f"{ticker}-{event_type}",
        "ticker": ticker,
        "company": f"{ticker} SA",
        "event_type": event_type,
        "structured_analysis": structured,
        "analysis_score": score,
    }


def test_dedup_keeps_one_per_ticker_and_backfills():
    """7 same-ticker top rows + 1 distinct lower company → 2 companies."""
    rows = [_row("TOW", "wyniki_finansowe", ["zysk 1 mln"], ann_id=f"tow{i}") for i in range(7)]
    rows.append(_row("ASB", "kontrakt_znaczacy", None))
    result = select_top_companies(rows, n=4)
    assert [r["ticker"] for r in result] == ["TOW", "ASB"]


def test_number_less_wyniki_finansowe_dropped_and_backfilled():
    rows = [
        _row("AAA", "wyniki_finansowe", []),  # number-less results → dropped
        _row("BBB", "kontrakt_znaczacy", None),
        _row("CCC", "dywidenda", None),
    ]
    result = select_top_companies(rows, n=2)
    assert [r["ticker"] for r in result] == ["BBB", "CCC"]


def test_qualitative_events_kept_with_empty_key_numbers():
    rows = [
        _row("BBB", "kontrakt_znaczacy", None),
        _row("CCC", "dywidenda", None),
        _row("DDD", "zmiana_zarzadu", []),
    ]
    result = select_top_companies(rows, n=5)
    assert [r["ticker"] for r in result] == ["BBB", "CCC", "DDD"]


def test_wyniki_sprzedazowe_treated_like_wyniki_finansowe():
    rows = [
        _row("AAA", "wyniki_sprzedazowe", []),  # dropped
        _row("BBB", "wyniki_sprzedazowe", ["sprzedaz 10 mln"]),  # kept
    ]
    result = select_top_companies(rows, n=5)
    assert [r["ticker"] for r in result] == ["BBB"]


def test_no_ticker_rows_skipped():
    rows = [
        {"announcement_id": "x", "ticker": "", "event_type": "dywidenda",
         "structured_analysis": "{}"},
        _row("AAA", "dywidenda", None),
    ]
    result = select_top_companies(rows, n=5)
    assert [r["ticker"] for r in result] == ["AAA"]


def test_order_preserved():
    rows = [
        _row("AAA", "dywidenda", None, score=150.0),
        _row("BBB", "dywidenda", None, score=120.0),
        _row("CCC", "dywidenda", None, score=100.0),
    ]
    result = select_top_companies(rows, n=5)
    assert [r["ticker"] for r in result] == ["AAA", "BBB", "CCC"]


def test_unparseable_structured_analysis_treated_as_empty_key_numbers():
    """A number-less (unparseable) wyniki_finansowe row is dropped."""
    bad = _row("AAA", "wyniki_finansowe", ["1 mln"])
    bad["structured_analysis"] = "{not valid json at all >>>"
    rows = [bad, _row("BBB", "kontrakt_znaczacy", None)]
    result = select_top_companies(rows, n=5)
    assert [r["ticker"] for r in result] == ["BBB"]


def test_takes_at_most_n():
    rows = [_row(f"T{i}", "dywidenda", None) for i in range(10)]
    result = select_top_companies(rows, n=3)
    assert len(result) == 3
    assert [r["ticker"] for r in result] == ["T0", "T1", "T2"]


def test_number_dependent_set_contents():
    assert NUMBER_DEPENDENT_EVENT_TYPES == {"wyniki_finansowe", "wyniki_sprzedazowe"}
