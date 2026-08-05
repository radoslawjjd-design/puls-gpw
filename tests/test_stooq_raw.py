"""Deciding whether a stooq per-symbol download is the unadjusted series (PUL-96).

Every fixture value below was measured, not invented — BAC on real sessions, read
from `d_pl_txt/data/daily/pl/nc stocks/bac.txt` and from a per-symbol download taken
with `o=1111111`. The pairing matters: on 2025-08-27..29 the bulk archive carries
fractional volumes (impossible for a share count, so those rows are adjusted) and the
raw download differs there by the 3.42% dividend factor, while on 2026-07-20..21 the
factor has reached 1.0 and both agree exactly.
"""
import pytest

from src.stooq_raw import (
    AdjustedSeriesError,
    UnverifiableSeriesError,
    adjusted_dates,
    assert_unadjusted,
    normalise_close,
)

# Bulk archive — the known-adjusted reference.
_BULK = [
    {"date": "2025-08-27", "close": 3.20048, "volume": 16393.4296574873},
    {"date": "2025-08-28", "close": 3.17147, "volume": 8750.5399237903},
    {"date": "2025-08-29", "close": 3.17147, "volume": 6707.9543724978},
    {"date": "2026-07-20", "close": 3.62, "volume": 3657.0},
    {"date": "2026-07-21", "close": 3.52, "volume": 4462.0},
]

# Per-symbol download with o=1111111 — the unadjusted series we want.
_RAW = [
    {"date": "2025-08-27", "close": 3.3100021292155, "volume": 15851.0},
    {"date": "2025-08-28", "close": 3.2799993915735, "volume": 8461.0},
    {"date": "2025-08-29", "close": 3.2799993915735, "volume": 6486.0},
    {"date": "2026-07-20", "close": 3.62, "volume": 3657.0},
    {"date": "2026-07-21", "close": 3.52, "volume": 4462.0},
]

# Per-symbol download WITHOUT o= — adjusted, and the case the guard exists to catch.
# Volumes are whole here on purpose: that is what the HTML view shows, and a maintainer
# tempted to replace the guard with a fractional-volume test would be reading exactly
# this. The guard must reject it on the closes alone.
_ADJUSTED_DOWNLOAD = [
    {"date": "2025-08-27", "close": 3.20048, "volume": 16393.0},
    {"date": "2025-08-28", "close": 3.17147, "volume": 8751.0},
    {"date": "2025-08-29", "close": 3.17147, "volume": 6708.0},
    {"date": "2026-07-20", "close": 3.62, "volume": 3657.0},
    {"date": "2026-07-21", "close": 3.52, "volume": 4462.0},
]


def test_the_adjusted_dates_are_exactly_the_fractional_volume_rows():
    assert adjusted_dates(_BULK) == {"2025-08-27", "2025-08-28", "2025-08-29"}


def test_a_whole_share_count_is_never_treated_as_adjusted():
    # 2026-07-20/21 sit after the last ex-dividend date, so the factor is 1.0 and the
    # bulk archive is raw there. Treating them as adjusted would compare two identical
    # series and reject every good file.
    assert "2026-07-20" not in adjusted_dates(_BULK)


def test_a_raw_download_passes_the_guard():
    assert_unadjusted(_RAW, _BULK)


def test_an_adjusted_download_is_rejected():
    with pytest.raises(AdjustedSeriesError) as exc:
        assert_unadjusted(_ADJUSTED_DOWNLOAD, _BULK)
    # The operator needs to know what to do about it, not just that it failed.
    assert "o=1111111" in str(exc.value)


def test_a_download_that_cannot_be_checked_is_refused_rather_than_guessed():
    # No overlap with any known-adjusted date: the file may be fine, but nothing here
    # can show that, and writing an unverified series is the failure this guards.
    recent_only = [r for r in _RAW if r["date"].startswith("2026")]
    with pytest.raises(UnverifiableSeriesError):
        assert_unadjusted(recent_only, _BULK)


def test_a_reference_with_no_adjusted_rows_cannot_verify_anything():
    clean_bulk = [r for r in _BULK if r["date"].startswith("2026")]
    with pytest.raises(UnverifiableSeriesError):
        assert_unadjusted(_RAW, clean_bulk)


def test_float_round_trip_noise_is_normalised_back_onto_the_tick():
    # stooq divides the adjusted value by the factor, leaving noise ~1e-6.
    assert normalise_close(3.2799993915735) == 3.28
    assert normalise_close(3.139996958116) == 3.14
    assert normalise_close(2.8600024334065) == 2.86


def test_normalising_leaves_a_value_that_is_already_clean_alone():
    assert normalise_close(3.62) == 3.62
