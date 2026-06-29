"""Round-trip smoke test for ETF BigQuery layer (PUL-67).

Verifies:
- create_etf_instruments_table_if_not_exists() creates table
- create_etf_quotes_table_if_not_exists() creates table
- merge_etf_instruments(): INSERT + UPDATE paths
- merge_etf_quotes(): INSERT + UPDATE paths
- list_distinct_tickers() includes ETF ticker via UNION

Cleans up sentinel rows on exit.

Run with:
    uv run python scripts/test_bq_etf_quotes.py

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
    _ETF_INSTRUMENTS_TABLE_NAME,
    _ETF_QUOTES_TABLE_NAME,
    create_etf_instruments_table_if_not_exists,
    create_etf_quotes_table_if_not_exists,
    ensure_etf_instruments_schema_current,
    ensure_etf_quotes_schema_current,
    merge_etf_instruments,
    merge_etf_quotes,
    list_distinct_tickers,
)

SENTINEL_TICKER = "_TEST_ETF_MERGE_"
SENTINEL_DATE = date(2000, 1, 1)
SENTINEL_DATE_STR = SENTINEL_DATE.isoformat()


def _query_etf_instrument(client, ticker: str) -> list:
    table = _table_ref(client, _ETF_INSTRUMENTS_TABLE_NAME)
    return list(client.query(
        f"SELECT ticker, name, instrument_type FROM `{table}` WHERE ticker = '{ticker}'"
    ).result())


def _query_etf_quote(client, ticker: str, snapshot_date: str) -> list:
    table = _table_ref(client, _ETF_QUOTES_TABLE_NAME)
    return list(client.query(
        f"SELECT ticker, kurs_zamkniecia FROM `{table}` "
        f"WHERE ticker = '{ticker}' AND snapshot_date = '{snapshot_date}'"
    ).result())


def _delete_sentinel(client) -> None:
    instr_table = _table_ref(client, _ETF_INSTRUMENTS_TABLE_NAME)
    quotes_table = _table_ref(client, _ETF_QUOTES_TABLE_NAME)
    client.query(f"DELETE FROM `{instr_table}` WHERE ticker = '{SENTINEL_TICKER}'").result()
    client.query(
        f"DELETE FROM `{quotes_table}` WHERE ticker = '{SENTINEL_TICKER}' "
        f"AND snapshot_date = '{SENTINEL_DATE_STR}'"
    ).result()


def main() -> None:
    client = _get_client()

    # --- 1. Table creation ---
    create_etf_instruments_table_if_not_exists()
    ensure_etf_instruments_schema_current()
    create_etf_quotes_table_if_not_exists()
    ensure_etf_quotes_schema_current()
    print("OK: tables created/verified")

    try:
        # --- 2. etf_instruments INSERT path ---
        merge_etf_instruments([{
            "ticker": SENTINEL_TICKER,
            "name": "Test ETF",
            "isin": "XX0000000000",
            "instrument_type": "ETF",
            "created_at": "2000-01-01T12:00:00+00:00",
            "updated_at": "2000-01-01T12:00:00+00:00",
        }])
        rows = _query_etf_instrument(client, SENTINEL_TICKER)
        assert len(rows) == 1, f"Expected 1 instrument row after INSERT, got {len(rows)}"
        assert rows[0].name == "Test ETF"
        assert rows[0].instrument_type == "ETF"
        print("OK: etf_instruments INSERT path OK")

        # --- 3. etf_instruments UPDATE path ---
        merge_etf_instruments([{
            "ticker": SENTINEL_TICKER,
            "name": "Test ETF Updated",
            "isin": "XX0000000000",
            "instrument_type": "ETF",
            "created_at": "2000-01-01T12:00:00+00:00",
            "updated_at": "2000-01-01T13:00:00+00:00",
        }])
        rows = _query_etf_instrument(client, SENTINEL_TICKER)
        assert len(rows) == 1, f"Expected 1 row after UPDATE (no duplicate), got {len(rows)}"
        assert rows[0].name == "Test ETF Updated"
        print("OK: etf_instruments UPDATE path OK")

        # --- 4. etf_quotes INSERT path ---
        merge_etf_quotes([{
            "ticker": SENTINEL_TICKER,
            "snapshot_date": SENTINEL_DATE_STR,
            "kurs_zamkniecia": 72.81,
            "zmiana_procentowa": -0.25,
            "zmiana_kwotowa": -0.18,
            "kurs_odn": 72.99,
            "kurs_otwarcia": 73.10,
            "kurs_min": 72.50,
            "kurs_max": 73.20,
            "wolumen_skum": 1000.0,
            "fetched_at": "2000-01-01T12:00:00+00:00",
        }])
        rows = _query_etf_quote(client, SENTINEL_TICKER, SENTINEL_DATE_STR)
        assert len(rows) == 1, f"Expected 1 quote row after INSERT, got {len(rows)}"
        assert rows[0].kurs_zamkniecia == 72.81, f"Got {rows[0].kurs_zamkniecia}"
        print("OK: etf_quotes INSERT path OK")

        # --- 5. etf_quotes UPDATE path ---
        merge_etf_quotes([{
            "ticker": SENTINEL_TICKER,
            "snapshot_date": SENTINEL_DATE_STR,
            "kurs_zamkniecia": 73.50,
            "zmiana_procentowa": 0.70,
            "zmiana_kwotowa": 0.51,
            "kurs_odn": 72.99,
            "kurs_otwarcia": 73.10,
            "kurs_min": 72.50,
            "kurs_max": 73.60,
            "wolumen_skum": 2000.0,
            "fetched_at": "2000-01-01T13:00:00+00:00",
        }])
        rows = _query_etf_quote(client, SENTINEL_TICKER, SENTINEL_DATE_STR)
        assert len(rows) == 1, f"Expected 1 row after UPDATE (no duplicate), got {len(rows)}"
        assert rows[0].kurs_zamkniecia == 73.50, f"Got {rows[0].kurs_zamkniecia}"
        print("OK: etf_quotes UPDATE path OK")

        # --- 6. list_distinct_tickers UNION ---
        tickers = list_distinct_tickers()
        assert SENTINEL_TICKER in tickers, (
            f"ETF ticker {SENTINEL_TICKER} not found in list_distinct_tickers() result. "
            f"Got {tickers[:5]}..."
        )
        print(f"OK: list_distinct_tickers() includes ETF ticker (total: {len(tickers)} tickers)")

    finally:
        _delete_sentinel(client)
        print("OK: Sentinel rows cleaned up")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
