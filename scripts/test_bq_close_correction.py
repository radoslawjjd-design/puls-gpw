"""Round-trip smoke test for the close-correction MERGE against real BigQuery (PUL-98).

Runs against a THROWAWAY table (company_daily_stats_rt_<uuid>) by monkeypatching the
module-level table-name constant — never touches the real table. Verifies:
  1. a matched key -> close, its two derived columns and source are corrected
  2. the observed-session columns (OHLC, turnover, trades, fetched_at, kurs_odn) survive
  3. an unmatched (ticker, snapshot_date) -> nothing inserted, row count unchanged
  4. a duplicated source batch -> the newest fetched_at wins, deterministically

Mocks cannot prove any of this: the column list only becomes real when BigQuery
executes the UPDATE SET.

Run with:
    uv run python scripts/test_bq_close_correction.py

Requires ADC: gcloud auth application-default login
"""
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import db.bigquery as bq
from db.bigquery import _get_client, _table_ref
from google.cloud import bigquery as gbq

# The columns a correction must never touch, with the values seeded in step 0.
_UNTOUCHED = {
    "kurs_otwarcia": 101.0,
    "kurs_min": 99.5,
    "kurs_max": 103.0,
    "wartosc_obrotu": 1_234_000.0,
    "liczba_transakcji": 42,
    "kurs_odn": 100.5,
}

_SEEDED = {
    "ticker": "_RT_TEST_",
    "snapshot_date": "2000-01-02",
    "kurs_zamkniecia": 100.0,
    "zmiana_procentowa": -0.5,
    "zmiana_kwotowa": -0.5,
    "source": "bankier",
    "fetched_at": "2000-01-02T17:15:00+00:00",
    **_UNTOUCHED,
}


def _one_row(client, ref, snapshot_date="2000-01-02"):
    rows = list(
        client.query(
            f"SELECT * FROM `{ref}` WHERE snapshot_date = '{snapshot_date}'"
        ).result()
    )
    assert len(rows) == 1, f"expected exactly 1 row for {snapshot_date}, got {len(rows)}"
    return rows[0]


def main() -> None:
    client = _get_client()
    real_name = bq._COMPANY_DAILY_STATS_TABLE_NAME
    rt_name = f"{real_name}_rt_{uuid.uuid4().hex[:8]}"
    rt_ref = _table_ref(client, rt_name)
    rt_table = gbq.Table(rt_ref, schema=bq._COMPANY_DAILY_STATS_SCHEMA)
    rt_table.expires = datetime.now(timezone.utc) + timedelta(hours=24)
    client.create_table(rt_table)
    bq._COMPANY_DAILY_STATS_TABLE_NAME = rt_name
    try:
        # 0. seed one row through the ordinary upsert path
        bq.merge_company_daily_stats([_SEEDED])
        seeded = _one_row(client, rt_ref)
        assert seeded.kurs_zamkniecia == 100.0 and seeded.source == "bankier"
        print("OK: seeded a bankier-sourced row")

        # 1. correct it
        corrected = bq.merge_company_daily_stats_close_correction([{
            "ticker": "_RT_TEST_",
            "snapshot_date": "2000-01-02",
            "kurs_zamkniecia": 102.5,
            "zmiana_procentowa": 1.99,
            "zmiana_kwotowa": 2.0,
            "source": "gpw-archive",
            "fetched_at": "2026-07-28T09:00:00+00:00",
        }])
        assert corrected == 1, f"expected 1 corrected row, got {corrected}"
        row = _one_row(client, rt_ref)
        assert row.kurs_zamkniecia == 102.5, f"close not corrected: {row.kurs_zamkniecia}"
        assert row.zmiana_procentowa == 1.99, f"pct not corrected: {row.zmiana_procentowa}"
        assert row.zmiana_kwotowa == 2.0, f"delta not corrected: {row.zmiana_kwotowa}"
        assert row.source == "gpw-archive", f"source not corrected: {row.source}"
        print("OK: close, both derived columns and source corrected")

        # 2. the observed session survived
        for column, expected in _UNTOUCHED.items():
            actual = getattr(row, column)
            assert actual == expected, f"{column} was modified: {actual} != {expected}"
        assert str(row.fetched_at).startswith("2000-01-02"), (
            f"fetched_at was modified: {row.fetched_at}"
        )
        print("OK: OHLC, turnover, trade count, kurs_odn and fetched_at untouched")

        # 3. an unmatched key inserts nothing
        before = list(client.query(f"SELECT COUNT(*) AS n FROM `{rt_ref}`").result())[0].n
        corrected = bq.merge_company_daily_stats_close_correction([{
            "ticker": "_RT_ABSENT_",
            "snapshot_date": "1999-12-31",
            "kurs_zamkniecia": 7.0,
            "zmiana_procentowa": 0.0,
            "zmiana_kwotowa": 0.0,
            "source": "gpw-archive",
            "fetched_at": "2026-07-28T09:00:00+00:00",
        }])
        after = list(client.query(f"SELECT COUNT(*) AS n FROM `{rt_ref}`").result())[0].n
        assert corrected == 0, f"expected 0 corrected for an absent key, got {corrected}"
        assert after == before, f"phantom row inserted: {before} -> {after}"
        print("OK: an unmatched (ticker, snapshot_date) inserted nothing")

        # 4. a duplicated batch resolves by newest fetched_at
        base = {
            "ticker": "_RT_TEST_",
            "snapshot_date": "2000-01-02",
            "zmiana_procentowa": 0.0,
            "zmiana_kwotowa": 0.0,
            "source": "gpw-archive",
        }
        bq.merge_company_daily_stats_close_correction([
            {**base, "kurs_zamkniecia": 1.0, "fetched_at": "2026-07-28T08:00:00+00:00"},
            {**base, "kurs_zamkniecia": 55.5, "fetched_at": "2026-07-28T10:00:00+00:00"},
        ])
        row = _one_row(client, rt_ref)
        assert row.kurs_zamkniecia == 55.5, (
            f"newest fetched_at did not win: {row.kurs_zamkniecia}"
        )
        print("OK: duplicated batch resolved to the newest fetched_at")

        print("PASS: all close-correction round-trip checks passed")
    finally:
        bq._COMPANY_DAILY_STATS_TABLE_NAME = real_name
        client.delete_table(rt_ref, not_found_ok=True)
        print("OK: throwaway table dropped")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
