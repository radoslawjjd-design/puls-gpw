"""Round-trip smoke test for merge_company_daily_stats against real BigQuery.

Verifies INSERT path (first merge) and UPDATE path (second merge with changed value),
including the PUL-98 provenance columns. Cleans up the sentinel row on exit.

The UPDATE assertions on `source` / `kurs_odn` are the point: mocked tests do not
parse SQL (context/foundation/lessons.md:211-235), and a column missing from the
MERGE's `UPDATE SET` would freeze at its first-write value with no error anywhere.

Run with:
    uv run python scripts/test_bq_company_stats_merge.py

Requires ADC: gcloud auth application-default login
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from db.bigquery import (
    _get_client,
    _table_ref,
    _COMPANY_DAILY_STATS_TABLE_NAME,
    delete_company_daily_stats_for_date,
    ensure_company_daily_stats_schema_current,
    merge_company_daily_stats,
)

SENTINEL_TICKER = "_TEST_MERGE_"
SENTINEL_DATE = date(2000, 1, 1)
SENTINEL_DATE_STR = SENTINEL_DATE.isoformat()

# PUL-98 columns, added after initial table creation — both must be NULLABLE for
# the additive ALTER TABLE ADD COLUMN migration path to work. Types are spelled out
# here rather than read from _COMPANY_DAILY_STATS_SCHEMA on purpose: this script
# checks the LIVE table against an independent expectation (lessons.md:317-320).
# BigQuery reports FLOAT64 columns as either name depending on the API surface.
_NEW_COLUMNS = {"source": {"STRING"}, "kurs_odn": {"FLOAT", "FLOAT64"}}


def _query_sentinel(client) -> list:
    table = _table_ref(client, _COMPANY_DAILY_STATS_TABLE_NAME)
    query = f"""
        SELECT ticker, snapshot_date, kurs_zamkniecia, source, kurs_odn,
               COUNT(*) OVER () AS cnt
        FROM `{table}`
        WHERE ticker = '{SENTINEL_TICKER}' AND snapshot_date = '{SENTINEL_DATE_STR}'
    """
    return list(client.query(query).result())


def _assert_live_schema(client) -> None:
    """Check the LIVE table, not the repo definition — lessons.md:317-320."""
    table = client.get_table(_table_ref(client, _COMPANY_DAILY_STATS_TABLE_NAME))
    live = {f.name: f for f in table.schema}
    for name, accepted_types in _NEW_COLUMNS.items():
        assert name in live, f"Live table is missing column {name} — migration did not run"
        assert live[name].mode == "NULLABLE", (
            f"Live column {name} is {live[name].mode}, expected NULLABLE"
        )
        assert live[name].field_type in accepted_types, (
            f"Live column {name} is {live[name].field_type}, "
            f"expected one of {sorted(accepted_types)}"
        )
    print("OK: live schema carries source + kurs_odn as NULLABLE")


def main() -> None:
    client = _get_client()

    ensure_company_daily_stats_schema_current()
    _assert_live_schema(client)

    row_v1 = {
        "ticker": SENTINEL_TICKER,
        "snapshot_date": SENTINEL_DATE_STR,
        "kurs_zamkniecia": 100.0,
        "zmiana_procentowa": 1.0,
        "zmiana_kwotowa": 1.0,
        "kurs_otwarcia": 99.0,
        "kurs_min": 98.0,
        "kurs_max": 101.0,
        "wartosc_obrotu": 50000.0,
        "liczba_transakcji": 10,
        "fetched_at": "2000-01-01T12:00:00+00:00",
        "source": "bankier",
        "kurs_odn": 99.5,
    }

    try:
        # --- Run 1: INSERT path ---
        merge_company_daily_stats([row_v1])
        rows = _query_sentinel(client)
        assert len(rows) == 1, f"Expected 1 row after INSERT, got {len(rows)}"
        assert rows[0].kurs_zamkniecia == 100.0, (
            f"Expected kurs_zamkniecia=100.0, got {rows[0].kurs_zamkniecia}"
        )
        assert rows[0].source == "bankier", f"Expected source=bankier, got {rows[0].source}"
        assert rows[0].kurs_odn == 99.5, f"Expected kurs_odn=99.5, got {rows[0].kurs_odn}"
        print(f"OK: INSERT path OK (source={rows[0].source}, kurs_odn={rows[0].kurs_odn})")

        # --- Run 2: UPDATE path ---
        row_v2 = {
            **row_v1,
            "kurs_zamkniecia": 105.0,
            "fetched_at": "2000-01-01T13:00:00+00:00",
            "source": "gpw",
            "kurs_odn": 100.0,
        }
        merge_company_daily_stats([row_v2])
        rows = _query_sentinel(client)
        assert len(rows) == 1, f"Expected 1 row after UPDATE (no duplicate), got {len(rows)}"
        assert rows[0].kurs_zamkniecia == 105.0, (
            f"Expected kurs_zamkniecia=105.0, got {rows[0].kurs_zamkniecia}"
        )
        # These two prove the MERGE's UPDATE SET carries the new columns. Without
        # them the row would still read source=bankier after an official write.
        assert rows[0].source == "gpw", (
            f"Expected source=gpw after UPDATE, got {rows[0].source} — "
            "the MERGE's UPDATE SET is missing the source column"
        )
        assert rows[0].kurs_odn == 100.0, (
            f"Expected kurs_odn=100.0 after UPDATE, got {rows[0].kurs_odn} — "
            "the MERGE's UPDATE SET is missing the kurs_odn column"
        )
        print(f"OK: UPDATE path OK (source={rows[0].source}, kurs_odn={rows[0].kurs_odn})")

    finally:
        delete_company_daily_stats_for_date(SENTINEL_DATE)
        print("OK: Sentinel row cleaned up")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
