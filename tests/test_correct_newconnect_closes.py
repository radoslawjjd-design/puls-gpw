"""Unit tests for scripts/correct_newconnect_closes.py pure logic (PUL-96).

No network, no BQ. Fixture values are measured BAC sessions: the stored (adjusted)
closes are what production holds today, and the raw closes are what a stooq download
with o=1111111 returns for the same days.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "correct_newconnect_closes.py"
_spec = importlib.util.spec_from_file_location("correct_newconnect_closes", _SCRIPT)
cnc = importlib.util.module_from_spec(_spec)
sys.modules["correct_newconnect_closes"] = cnc
_spec.loader.exec_module(cnc)

_FETCHED_AT = "2026-08-05T12:00:00+00:00"

# What production holds: adjusted close plus the percentage, which is already correct.
_STORED = {
    "2025-08-27": {"close": 3.2005, "pct": -3.22},
    "2025-08-28": {"close": 3.1715, "pct": -0.91},
    "2025-08-29": {"close": 3.1715, "pct": 0.0},
    "2026-07-20": {"close": 3.62, "pct": 1.12},
}

# What the raw download returns for the same days, noise and all.
_RAW = [
    {"date": "2025-08-27", "close": 3.3100021292155},
    {"date": "2025-08-28", "close": 3.2799993915735},
    {"date": "2025-08-29", "close": 3.2799993915735},
    {"date": "2026-07-20", "close": 3.62},
]


def _rows():
    return cnc.build_correction_rows("BAC", _RAW, _STORED, _FETCHED_AT)


def test_only_the_days_that_actually_differ_are_written():
    # 2026-07-20 already matches — the factor had reached 1.0 by then. Rewriting it
    # would burn a partition modification for no change.
    assert {r["snapshot_date"] for r in _rows()} == {
        "2025-08-27",
        "2025-08-28",
        "2025-08-29",
    }


def test_the_close_is_the_raw_price_snapped_back_onto_the_tick():
    by_date = {r["snapshot_date"]: r for r in _rows()}
    assert by_date["2025-08-28"]["kurs_zamkniecia"] == 3.28
    assert by_date["2025-08-27"]["kurs_zamkniecia"] == 3.31


def test_the_stored_percentage_is_carried_through_untouched():
    # A percentage change is invariant under a constant factor, so the stored value is
    # already right. Recomputing it from the raw series would be churn at best.
    by_date = {r["snapshot_date"]: r for r in _rows()}
    assert by_date["2025-08-27"]["zmiana_procentowa"] == -3.22
    assert by_date["2025-08-28"]["zmiana_procentowa"] == -0.91


def test_the_pln_change_is_derived_from_the_reference_not_from_the_previous_close():
    # Close-to-close differencing is wrong across exactly the events this repair is
    # about. -3.22% on a 3.31 close implies a reference of 3.41, so ~-0.11.
    by_date = {r["snapshot_date"]: r for r in _rows()}
    expected = 3.31 - 3.31 / (1 + -3.22 / 100)
    assert by_date["2025-08-27"]["zmiana_kwotowa"] == pytest.approx(expected)
    # Differencing would have given 3.31 - 3.28 = +0.03, the wrong sign entirely.
    assert by_date["2025-08-27"]["zmiana_kwotowa"] < 0


def test_a_flat_session_keeps_a_zero_change_rather_than_a_null():
    by_date = {r["snapshot_date"]: r for r in _rows()}
    assert by_date["2025-08-29"]["zmiana_kwotowa"] == pytest.approx(0.0)


def test_every_written_row_records_where_the_value_came_from():
    assert {r["source"] for r in _rows()} == {"stooq_raw"}


def test_a_day_the_table_does_not_hold_is_never_invented():
    # The MERGE has no INSERT branch, but a row built for an absent date would still
    # travel through the staging load for nothing — and the trading-day spine is
    # SELECT DISTINCT snapshot_date, so a stray date must never reach it.
    raw = _RAW + [{"date": "2025-08-30", "close": 9.99}]
    assert "2025-08-30" not in {
        r["snapshot_date"] for r in cnc.build_correction_rows("BAC", raw, _STORED, _FETCHED_AT)
    }


def test_a_row_with_no_stored_percentage_is_skipped_rather_than_nulled():
    # All four correction columns are assigned unconditionally, so writing None here
    # would blank a good zmiana_procentowa and the calendar sums that straight into
    # the day's P/L.
    stored = dict(_STORED)
    stored["2025-08-28"] = {"close": 3.1715, "pct": None}
    rows = cnc.build_correction_rows("BAC", _RAW, stored, _FETCHED_AT)
    assert "2025-08-28" not in {r["snapshot_date"] for r in rows}


def test_a_close_that_cannot_be_parsed_does_not_produce_a_row():
    raw = _RAW + [{"date": "2025-08-27", "close": None}]
    rows = cnc.build_correction_rows("BAC", raw, _STORED, _FETCHED_AT)
    assert all(r["kurs_zamkniecia"] is not None for r in rows)
