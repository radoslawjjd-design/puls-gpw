"""Round-trip smoke test for the broker-operations layer against real BigQuery (PUL-95).

Mocked tests do not parse SQL — see the reserved-keyword lesson in
context/foundation/lessons.md — so every hand-built statement this change adds
has to be executed once against the real thing.

Runs against THROWAWAY tables (<name>_rt_<uuid>) by monkeypatching the
module-level table-name constants; the real tables are never touched. Verifies:
  1. new external_id -> inserted
  2. re-merge same external_id with different values -> row unchanged
  3. duplicated source batch -> single row
  4. dividend aggregation, including the year list when the year has no data
  5. bulk position upsert + targeted delete
  6. wall-clock cost of merging a realistic 571-row batch

Run with:
    uv run python scripts/test_bq_broker_operations.py

Requires ADC: gcloud auth application-default login
"""
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import db.bigquery as bq
from db.bigquery import _get_client, _table_ref
from google.cloud import bigquery as gbq

USER = f"rt-user-{uuid.uuid4().hex[:8]}"
PORTFOLIO = "rt-pf-1"

# Cloud Run gives the commit endpoint 60s total. This primitive has never run
# inside an HTTP request before — both existing callers are batch jobs — so the
# threshold is a third of the budget, leaving room for the other two steps.
MERGE_BUDGET_SECONDS = 15.0
REALISTIC_BATCH = 571


def _operation(external_id: str, **overrides) -> dict:
    row = {
        "user_id": USER,
        "portfolio_id": PORTFOLIO,
        "broker": "xtb",
        "external_id": external_id,
        "op_type": "buy",
        "occurred_at": "2026-07-21T09:51:44.265000+00:00",
        "imported_at": "2026-07-29T10:00:00+00:00",
        "amount_pln": -4420.0,
        "raw_type": "Stock purchase",
        "ticker": "VOT",
        "instrument_name": "Votum",
        "volume": 100.0,
        "unit_price": 44.20,
        "comment": "OPEN BUY 100 @ 44.20",
        "source_file": "roundtrip.xlsx",
    }
    row.update(overrides)
    return row


def _make_throwaway(const_name: str, schema) -> tuple[str, str]:
    client = _get_client()
    real_name = getattr(bq, const_name)
    rt_name = f"{real_name}_rt_{uuid.uuid4().hex[:8]}"
    rt_ref = _table_ref(client, rt_name)
    table = gbq.Table(rt_ref, schema=schema)
    # expires must be set BEFORE create_table, or the table outlives the run.
    table.expires = datetime.now(timezone.utc) + timedelta(hours=24)
    client.create_table(table)
    setattr(bq, const_name, rt_name)
    return real_name, rt_ref


def _check_operations(rt_ref: str) -> None:
    client = _get_client()

    inserted = bq.merge_user_broker_operations([_operation("xtb:1")])
    assert inserted == 1, f"expected 1 inserted, got {inserted}"
    rows = list(client.query(f"SELECT * FROM `{rt_ref}`").result())
    assert len(rows) == 1 and rows[0].amount_pln == -4420.0
    print("OK: operations insert path")

    clobber = _operation("xtb:1", amount_pln=-9999.0, imported_at="2030-01-01T00:00:00+00:00")
    inserted = bq.merge_user_broker_operations([clobber])
    assert inserted == 0, f"expected 0 inserted for an existing external_id, got {inserted}"
    rows = list(client.query(f"SELECT * FROM `{rt_ref}`").result())
    assert len(rows) == 1 and rows[0].amount_pln == -4420.0, "existing operation was modified"
    print("OK: operations no-clobber path (re-import is a no-op)")

    dup = _operation("xtb:2")
    inserted = bq.merge_user_broker_operations([dup, _operation("xtb:2", amount_pln=-1.0)])
    assert inserted == 1, f"duplicated batch inserted {inserted} rows"
    rows = list(client.query(f"SELECT * FROM `{rt_ref}` WHERE external_id = 'xtb:2'").result())
    assert len(rows) == 1, f"duplicated batch produced {len(rows)} rows"
    print("OK: operations batch-dedup path")

    # Microsecond-distinct timestamps must survive the round trip: three PAS
    # payouts in the real IKZE file share a second.
    micro = [
        _operation(f"xtb:micro-{n}", op_type="dividend", ticker="PAS", amount_pln=amount,
                   occurred_at=f"2026-07-02T09:54:01.{frac}+00:00")
        for n, (amount, frac) in enumerate([(13.74, "332000"), (45.0, "278000"), (37.5, "218000")])
    ]
    bq.merge_user_broker_operations(micro)
    rows = list(client.query(
        f"SELECT DISTINCT occurred_at FROM `{rt_ref}` WHERE ticker = 'PAS'"
    ).result())
    assert len(rows) == 3, f"microsecond precision lost: {len(rows)} distinct stamps, expected 3"
    print("OK: microsecond precision preserved")


def _check_dividends() -> None:
    # Its own user id: the operations check already wrote PAS dividends for USER,
    # and an assertion that silently depends on an earlier step is not a check.
    div_user = f"{USER}-div"
    bq.merge_user_broker_operations([
        _operation("xtb:div-1", user_id=div_user, op_type="dividend", ticker="KRU",
                   amount_pln=722.0, occurred_at="2026-06-24T05:50:26.250000+00:00"),
        _operation("xtb:div-2", user_id=div_user, op_type="withholding_tax", ticker="KRU",
                   amount_pln=-137.18, occurred_at="2026-06-24T05:50:27.000000+00:00"),
        _operation("xtb:div-3", user_id=div_user, op_type="dividend", ticker="XTB",
                   amount_pln=100.0, occurred_at="2025-06-24T05:50:26.000000+00:00"),
    ])

    summary = bq.get_dividend_summary(div_user, PORTFOLIO, 2026)
    gross = summary["totals"]["gross"]
    tax = summary["totals"]["tax"]
    assert abs(gross - 722.0) < 0.005, f"gross {gross}, expected 722.00"
    assert abs(tax - (-137.18)) < 0.005, f"tax {tax}, expected -137.18"
    assert abs(summary["totals"]["net"] - 584.82) < 0.005
    print(f"OK: dividend aggregation (gross {gross:.2f}, tax {tax:.2f})")

    assert summary["years"] == [2025, 2026], f"year list {summary['years']}, expected [2025, 2026]"
    print("OK: year list returned alongside the totals")

    # The PUL-100 trap: a year with no payouts must still return the selector.
    empty = bq.get_dividend_summary(div_user, PORTFOLIO, 2019)
    assert empty["by_ticker"] == [], "expected no rows for 2019"
    assert empty["years"] == [2025, 2026], (
        f"year list vanished on an empty result: {empty['years']}"
    )
    print("OK: year list survives an empty result (meta-first join)")


def _check_positions(rt_ref: str) -> None:
    client = _get_client()

    written = bq.merge_user_portfolio_positions_bulk(USER, PORTFOLIO, [
        {"ticker": "CBF", "company_name": "CyberFolks", "shares": 11.0, "avg_buy_price": 188.40},
        {"ticker": "TOA", "company_name": "Toya", "shares": 412.0, "avg_buy_price": 10.02},
        {"ticker": "S2B", "company_name": "Syn2Bio", "shares": 4.0, "avg_buy_price": 0.01},
    ])
    assert written == 3, f"expected 3 positions written, got {written}"
    print("OK: bulk position insert")

    # Same wallet, a corrected price, and S2B deliberately absent from the batch.
    bq.merge_user_portfolio_positions_bulk(USER, PORTFOLIO, [
        {"ticker": "CBF", "company_name": "CyberFolks", "shares": 11.0, "avg_buy_price": 199.40},
    ])
    rows = {r.ticker: r for r in client.query(
        f"SELECT ticker, shares, avg_buy_price FROM `{rt_ref}` WHERE user_id = '{USER}'"
    ).result()}
    assert abs(rows["CBF"].avg_buy_price - 199.40) < 0.005, "update branch did not fire"
    assert "S2B" in rows, "a position absent from the batch was deleted — S2B must survive"
    print("OK: bulk upsert updates in place and leaves untouched holdings alone")

    removed = bq.delete_user_portfolio_positions(USER, PORTFOLIO, ["TOA"])
    assert removed == 1, f"expected 1 removed, got {removed}"
    rows = {r.ticker for r in client.query(
        f"SELECT ticker FROM `{rt_ref}` WHERE user_id = '{USER}'"
    ).result()}
    assert rows == {"CBF", "S2B"}, f"unexpected survivors: {sorted(rows)}"
    print("OK: targeted delete removed only the named ticker")

    assert bq.delete_user_portfolio_positions(USER, PORTFOLIO, []) == 0
    print("OK: empty delete list is a no-op")


def _check_merge_timing() -> None:
    batch = [_operation(f"xtb:perf-{n}") for n in range(REALISTIC_BATCH)]
    started = time.monotonic()
    inserted = bq.merge_user_broker_operations(batch)
    elapsed = time.monotonic() - started
    assert inserted == REALISTIC_BATCH, f"expected {REALISTIC_BATCH} inserted, got {inserted}"
    verdict = "OK" if elapsed < MERGE_BUDGET_SECONDS else "SLOW"
    print(f"{verdict}: {REALISTIC_BATCH}-row merge took {elapsed:.1f}s "
          f"(budget {MERGE_BUDGET_SECONDS:.0f}s of the 60s request limit)")
    assert elapsed < MERGE_BUDGET_SECONDS, (
        f"merge of {REALISTIC_BATCH} rows took {elapsed:.1f}s, over the {MERGE_BUDGET_SECONDS:.0f}s budget"
    )


def main() -> None:
    client = _get_client()
    ops_real, ops_ref = _make_throwaway(
        "_USER_BROKER_OPERATIONS_TABLE_NAME", bq._USER_BROKER_OPERATIONS_SCHEMA
    )
    pos_real, pos_ref = _make_throwaway(
        "_USER_PORTFOLIO_POSITIONS_TABLE_NAME", bq._USER_PORTFOLIO_POSITIONS_SCHEMA
    )
    try:
        _check_operations(ops_ref)
        _check_dividends()
        _check_positions(pos_ref)
        _check_merge_timing()
        print("\nAll broker-operations round-trip checks passed.")
    finally:
        bq._USER_BROKER_OPERATIONS_TABLE_NAME = ops_real
        bq._USER_PORTFOLIO_POSITIONS_TABLE_NAME = pos_real
        client.delete_table(ops_ref, not_found_ok=True)
        client.delete_table(pos_ref, not_found_ok=True)
        print("OK: throwaway tables dropped")


if __name__ == "__main__":
    main()
