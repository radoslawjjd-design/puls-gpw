"""Round-trip smoke test for merge_company_daily_stats against real BigQuery.

Verifies INSERT path (first merge) and UPDATE path (second merge with changed value).
Cleans up the sentinel row on exit.

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
    merge_company_daily_stats,
)

SENTINEL_TICKER = "_TEST_MERGE_"
SENTINEL_DATE = date(2000, 1, 1)
SENTINEL_DATE_STR = SENTINEL_DATE.isoformat()


def _query_sentinel(client) -> list:
    table = _table_ref(client, _COMPANY_DAILY_STATS_TABLE_NAME)
    query = f"""
        SELECT ticker, snapshot_date, kurs_zamkniecia, COUNT(*) OVER () AS cnt
        FROM `{table}`
        WHERE ticker = '{SENTINEL_TICKER}' AND snapshot_date = '{SENTINEL_DATE_STR}'
    """
    return list(client.query(query).result())


def main() -> None:
    client = _get_client()

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
    }

    try:
        # --- Run 1: INSERT path ---
        merge_company_daily_stats([row_v1])
        rows = _query_sentinel(client)
        assert len(rows) == 1, f"Expected 1 row after INSERT, got {len(rows)}"
        assert rows[0].kurs_zamkniecia == 100.0, (
            f"Expected kurs_zamkniecia=100.0, got {rows[0].kurs_zamkniecia}"
        )
        print("OK: INSERT path OK")

        # --- Run 2: UPDATE path ---
        row_v2 = {**row_v1, "kurs_zamkniecia": 105.0, "fetched_at": "2000-01-01T13:00:00+00:00"}
        merge_company_daily_stats([row_v2])
        rows = _query_sentinel(client)
        assert len(rows) == 1, f"Expected 1 row after UPDATE (no duplicate), got {len(rows)}"
        assert rows[0].kurs_zamkniecia == 105.0, (
            f"Expected kurs_zamkniecia=105.0, got {rows[0].kurs_zamkniecia}"
        )
        print("OK: UPDATE path OK")

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
