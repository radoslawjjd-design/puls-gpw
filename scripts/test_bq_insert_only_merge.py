"""Round-trip smoke test for insert-only MERGE against real BigQuery (PUL-92).

Runs against THROWAWAY tables (company_daily_stats_rt_<uuid> / etf_quotes_rt_<uuid>)
by monkeypatching the module-level table-name constants — never touches the real
tables. Verifies, per table:
  1. new key -> inserted
  2. re-merge same key with different values -> row unchanged (no clobber)
  3. duplicated source batch -> single row

Run with:
    uv run python scripts/test_bq_insert_only_merge.py

Requires ADC: gcloud auth application-default login
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import db.bigquery as bq
from db.bigquery import _get_client, _table_ref
from google.cloud import bigquery as gbq


def _run_case(label: str, const_name: str, schema, merge_fn, base_row: dict) -> None:
    client = _get_client()
    real_name = getattr(bq, const_name)
    rt_name = f"{real_name}_rt_{uuid.uuid4().hex[:8]}"
    rt_ref = _table_ref(client, rt_name)
    client.create_table(gbq.Table(rt_ref, schema=schema))
    setattr(bq, const_name, rt_name)
    try:
        # 1. new key -> inserted
        inserted = merge_fn([base_row])
        assert inserted == 1, f"{label}: expected 1 inserted, got {inserted}"
        rows = list(client.query(f"SELECT * FROM `{rt_ref}`").result())
        assert len(rows) == 1, f"{label}: expected 1 row, got {len(rows)}"
        assert rows[0].kurs_zamkniecia == base_row["kurs_zamkniecia"]
        print(f"OK: {label} insert path")

        # 2. re-merge same key, different value -> unchanged
        clobber = {**base_row, "kurs_zamkniecia": 999.0, "fetched_at": "2001-01-01T00:00:00+00:00"}
        inserted = merge_fn([clobber])
        assert inserted == 0, f"{label}: expected 0 inserted on existing key, got {inserted}"
        rows = list(client.query(f"SELECT * FROM `{rt_ref}`").result())
        assert len(rows) == 1 and rows[0].kurs_zamkniecia == base_row["kurs_zamkniecia"], (
            f"{label}: existing row was modified (kurs={rows[0].kurs_zamkniecia})"
        )
        print(f"OK: {label} no-clobber path")

        # 3. duplicated source batch, new key -> single row
        dup = {**base_row, "snapshot_date": "2000-01-03"}
        inserted = merge_fn([dup, {**dup, "kurs_zamkniecia": 111.0}])
        assert inserted == 1, f"{label}: expected 1 inserted from duplicated batch, got {inserted}"
        rows = list(client.query(
            f"SELECT * FROM `{rt_ref}` WHERE snapshot_date = '2000-01-03'"
        ).result())
        assert len(rows) == 1, f"{label}: duplicated batch produced {len(rows)} rows"
        print(f"OK: {label} batch-dedup path")
    finally:
        setattr(bq, const_name, real_name)
        client.delete_table(rt_ref, not_found_ok=True)
        print(f"OK: {label} throwaway table dropped")


def main() -> None:
    company_row = {
        "ticker": "_RT_TEST_",
        "snapshot_date": "2000-01-02",
        "kurs_zamkniecia": 100.0,
        "zmiana_kwotowa": 1.0,
        "fetched_at": "2000-01-02T12:00:00+00:00",
    }
    etf_row = {**company_row, "kurs_odn": 99.0, "wolumen_skum": 1234.0}
    del etf_row["zmiana_kwotowa"]

    _run_case(
        "company_daily_stats",
        "_COMPANY_DAILY_STATS_TABLE_NAME",
        bq._COMPANY_DAILY_STATS_SCHEMA,
        bq.merge_company_daily_stats_insert_only,
        company_row,
    )
    _run_case(
        "etf_quotes",
        "_ETF_QUOTES_TABLE_NAME",
        bq._ETF_QUOTES_SCHEMA,
        bq.merge_etf_quotes_insert_only,
        etf_row,
    )
    print("PASS: all insert-only round-trip checks passed")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
