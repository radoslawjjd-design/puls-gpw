"""Unit tests for scripts/correct_official_closes.py pure logic (PUL-98).

No network, no BigQuery. The behaviours pinned here are the ones where a wrong
answer would be silent: a name mapped to the wrong ticker writes a plausible
price against the wrong company, and a percentage recomputed close-to-close is
wrong on exactly the days a corporate action happened.
"""
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "correct_official_closes.py"
_spec = importlib.util.spec_from_file_location("correct_official_closes", _SCRIPT)
cc = importlib.util.module_from_spec(_spec)
sys.modules["correct_official_closes"] = cc
_spec.loader.exec_module(cc)

_FETCHED_AT = "2026-07-28T09:00:00+00:00"


# ── session enumeration ───────────────────────────────────────────────────────

def test_session_dates_skips_weekends():
    """Weekends are never sessions; probing them wastes ~40% of the fetches."""
    dates = cc.session_dates(date(2026, 7, 20), date(2026, 7, 27))

    assert dates == [
        date(2026, 7, 20), date(2026, 7, 21), date(2026, 7, 22),
        date(2026, 7, 23), date(2026, 7, 24), date(2026, 7, 27),
    ]


def test_session_dates_is_empty_when_the_range_inverts():
    assert cc.session_dates(date(2026, 7, 27), date(2026, 7, 20)) == []


# ── name mapping ──────────────────────────────────────────────────────────────

def test_name_map_keeps_only_exact_hits_against_known_tickers():
    feed = {
        "ALE": {"company_name": "ALLEGRO"},
        "PKO": {"company_name": "PKOBP"},
        "GHOST": {"company_name": "GHOSTCO"},   # not in companies
        "NONAME": {"company_name": None},
    }

    name_map, rejected = cc.build_name_map(feed, {"ALE", "PKO"})

    assert name_map == {"ALLEGRO": "ALE", "PKOBP": "PKO"}
    assert "GHOSTCO" in rejected


def test_name_map_drops_a_name_two_tickers_claim():
    """An ambiguous name is the mis-mapping failure mode — drop it, never guess."""
    feed = {
        "AAA": {"company_name": "DUPLICATE"},
        "BBB": {"company_name": "DUPLICATE"},
        "PKO": {"company_name": "PKOBP"},
    }

    name_map, rejected = cc.build_name_map(feed, {"AAA", "BBB", "PKO"})

    assert "DUPLICATE" not in name_map
    assert "DUPLICATE" in rejected
    assert name_map["PKOBP"] == "PKO"


# ── the percentage must come from the archive ─────────────────────────────────

def test_zmiana_kwotowa_is_derived_from_the_archive_percentage():
    """close - close/(1 + pct/100) — the change against the reference price."""
    assert cc.derive_zmiana_kwotowa(108.0, 1.33) == pytest.approx(1.4175466, abs=1e-6)
    assert cc.derive_zmiana_kwotowa(108.0, 0.0) == pytest.approx(0.0)
    assert cc.derive_zmiana_kwotowa(108.0, None) is None
    assert cc.derive_zmiana_kwotowa(None, 1.33) is None
    # A limit-down to zero would make the reference price undefined.
    assert cc.derive_zmiana_kwotowa(108.0, -100.0) is None


def test_correction_takes_the_percentage_from_the_archive_not_from_the_closes():
    """ECHO on 2026-06-24 moved +0.10% officially and -7.40% close-to-close: the
    difference is the dividend. Recomputing here would rebuild the GH #191 defect."""
    sheet = {"ECHO": {"kurs_zamkniecia": 5.02, "zmiana_procentowa": 0.10}}
    name_map = {"ECHO": "ECH"}
    stored = {"ECH": {"close": 5.41, "pct": -7.40}}   # naive close-to-close

    rows = cc.build_correction_rows(date(2026, 6, 24), sheet, name_map, stored, _FETCHED_AT)

    assert len(rows) == 1
    assert rows[0]["zmiana_procentowa"] == pytest.approx(0.10)
    assert rows[0]["zmiana_kwotowa"] == pytest.approx(5.02 - 5.02 / 1.001, abs=1e-6)


# ── correction row construction ───────────────────────────────────────────────

def test_correction_rows_skip_values_that_already_match():
    """Rewriting a correct row is churn that hides what the run actually changed."""
    sheet = {
        "ALLEGRO": {"kurs_zamkniecia": 44.735, "zmiana_procentowa": -0.33},
        "PKOBP": {"kurs_zamkniecia": 108.0, "zmiana_procentowa": 1.33},
    }
    name_map = {"ALLEGRO": "ALE", "PKOBP": "PKO"}
    stored = {"ALE": {"close": 44.735, "pct": -0.33}, "PKO": {"close": 107.9, "pct": 1.24}}

    rows = cc.build_correction_rows(date(2026, 7, 24), sheet, name_map, stored, _FETCHED_AT)

    assert [r["ticker"] for r in rows] == ["PKO"]
    assert rows[0]["snapshot_date"] == "2026-07-24"
    assert rows[0]["kurs_zamkniecia"] == pytest.approx(108.0)
    assert rows[0]["source"] == "archive"
    assert rows[0]["fetched_at"] == _FETCHED_AT


def test_correction_rows_ignore_names_that_did_not_map_and_rows_we_never_stored():
    sheet = {
        "UNKNOWNCO": {"kurs_zamkniecia": 10.0, "zmiana_procentowa": 1.0},
        "PKOBP": {"kurs_zamkniecia": 108.0, "zmiana_procentowa": 1.33},
        "ALLEGRO": {"kurs_zamkniecia": 44.735, "zmiana_procentowa": -0.33},
    }
    name_map = {"PKOBP": "PKO", "ALLEGRO": "ALE"}
    stored = {"PKO": {"close": 107.9, "pct": 1.24}}   # ALE has no row on this date

    rows = cc.build_correction_rows(date(2026, 7, 24), sheet, name_map, stored, _FETCHED_AT)

    assert [r["ticker"] for r in rows] == ["PKO"]


def test_correction_rows_skip_a_session_the_archive_left_blank():
    """No trades means no close; writing None would blank a real stored price."""
    sheet = {"PKOBP": {"kurs_zamkniecia": None, "zmiana_procentowa": None}}

    rows = cc.build_correction_rows(
        date(2026, 7, 24), sheet, {"PKOBP": "PKO"},
        {"PKO": {"close": 107.9, "pct": 1.24}}, _FETCHED_AT
    )

    assert rows == []


# ── phantom trading days ──────────────────────────────────────────────────────

def test_phantom_dates_include_a_weekend_we_never_probed():
    """2026-06-27 is a Saturday with 739 rows in production. The run only probes
    weekdays, so a weekend phantom would be invisible unless it is judged without
    a fetch — GPW never trades on a Saturday, and that needs no confirmation."""
    phantoms = cc.find_phantom_dates(
        traded={date(2026, 7, 24)},
        probed={date(2026, 7, 24)},
        stored_counts={date(2026, 6, 27): 739, date(2026, 7, 24): 700},
    )

    assert phantoms == [(date(2026, 6, 27), 739)]


def test_phantom_dates_include_a_weekday_the_archive_reported_as_no_session():
    """A holiday we stored rows for is the same defect as a weekend one."""
    phantoms = cc.find_phantom_dates(
        traded=set(),
        probed={date(2026, 5, 1)},
        stored_counts={date(2026, 5, 1): 700},
    )

    assert phantoms == [(date(2026, 5, 1), 700)]


def test_an_unprobed_weekday_is_not_called_a_phantom():
    """A date we never reached says nothing about whether the market traded —
    reporting it would turn a network gap into a data-integrity claim."""
    phantoms = cc.find_phantom_dates(
        traded=set(), probed=set(), stored_counts={date(2026, 7, 24): 700}
    )

    assert phantoms == []


# ── chunking and cache ────────────────────────────────────────────────────────

def test_rows_are_grouped_by_year_ascending():
    """One MERGE per year stays far below BigQuery's 4000-partition job cap."""
    rows = [
        {"snapshot_date": "2026-01-02"}, {"snapshot_date": "2025-06-01"},
        {"snapshot_date": "2026-07-24"},
    ]

    grouped = cc.group_rows_by_year(rows)

    assert [year for year, _ in grouped] == ["2025", "2026"]
    assert len(grouped[1][1]) == 2


def test_cache_path_is_stable_per_date(tmp_path):
    """The cache is also the resume mechanism, so the name must not drift."""
    first = cc.cache_path(tmp_path, date(2026, 7, 24))
    second = cc.cache_path(tmp_path, date(2026, 7, 24))

    assert first == second
    assert first.parent == tmp_path
    assert "2026-07-24" in first.name


def test_a_correct_close_with_a_wrong_percentage_is_still_corrected():
    """Measured over 5 sampled sessions: 8 of 1 156 rows whose close already agrees
    carry a percentage that does not. The calendar renders that number directly, so
    a right price with a wrong move is still a visible defect."""
    sheet = {"PKOBP": {"kurs_zamkniecia": 108.0, "zmiana_procentowa": 1.33}}
    stored = {"PKO": {"close": 108.0, "pct": -7.40}}

    rows = cc.build_correction_rows(
        date(2026, 7, 24), sheet, {"PKOBP": "PKO"}, stored, _FETCHED_AT
    )

    assert len(rows) == 1
    assert rows[0]["kurs_zamkniecia"] == pytest.approx(108.0)
    assert rows[0]["zmiana_procentowa"] == pytest.approx(1.33)


def test_a_row_agreeing_on_both_close_and_percentage_is_left_alone():
    sheet = {"PKOBP": {"kurs_zamkniecia": 108.0, "zmiana_procentowa": 1.33}}
    stored = {"PKO": {"close": 108.0, "pct": 1.33}}

    assert cc.build_correction_rows(
        date(2026, 7, 24), sheet, {"PKOBP": "PKO"}, stored, _FETCHED_AT
    ) == []
