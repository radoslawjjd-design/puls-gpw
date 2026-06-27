"""Round-trip smoke test for user_portfolio_positions BQ functions against real BigQuery.

Verifies: create/ensure → upsert (INSERT path) → upsert again (UPDATE, no dup) →
list with pricing JOIN → delete → verify empty.

Run with:
    uv run python scripts/test_bq_user_portfolio_positions.py

Requires ADC: gcloud auth application-default login
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from db.bigquery import (
    _get_client,
    _table_ref,
    _USER_PORTFOLIO_POSITIONS_TABLE_NAME,
    create_user_portfolio_positions_table_if_not_exists,
    delete_user_portfolio_position,
    ensure_user_portfolio_positions_schema_current,
    list_user_portfolio_positions,
    upsert_user_portfolio_position,
)

TEST_USER_ID = "e2e-test-user"
TEST_TICKER = "PKO"


def _count_rows(client) -> int:
    table = _table_ref(client, _USER_PORTFOLIO_POSITIONS_TABLE_NAME)
    query = f"SELECT COUNT(*) AS cnt FROM `{table}` WHERE user_id = '{TEST_USER_ID}'"
    rows = list(client.query(query).result())
    return rows[0].cnt


def main() -> None:
    client = _get_client()

    try:
        # --- Step 1: create / ensure ---
        create_user_portfolio_positions_table_if_not_exists()
        ensure_user_portfolio_positions_schema_current()
        print("OK: table created / schema current")

        # --- Step 2: upsert INSERT path ---
        upsert_user_portfolio_position(TEST_USER_ID, TEST_TICKER, "PKO BP SA", 10.0, 40.0)
        cnt = _count_rows(client)
        assert cnt == 1, f"Expected 1 row after INSERT, got {cnt}"
        print("OK: INSERT path — 1 row")

        # --- Step 3: upsert UPDATE path (same key, different shares) ---
        upsert_user_portfolio_position(TEST_USER_ID, TEST_TICKER, "PKO BP SA", 15.0, 42.0)
        cnt = _count_rows(client)
        assert cnt == 1, f"Expected 1 row after UPDATE (no dup), got {cnt}"
        print("OK: UPDATE path — still 1 row, no duplicate")

        # --- Step 4: list with pricing JOIN ---
        positions = list_user_portfolio_positions(TEST_USER_ID)
        assert len(positions) == 1, f"Expected 1 position, got {len(positions)}"
        pos = positions[0]
        assert pos["ticker"] == TEST_TICKER, f"Wrong ticker: {pos['ticker']}"
        assert pos["shares"] == 15.0, f"Expected shares=15.0, got {pos['shares']}"
        assert pos["avg_buy_price"] == 42.0, f"Expected avg_buy_price=42.0, got {pos['avg_buy_price']}"
        assert pos["current_price"] is None or isinstance(pos["current_price"], float), (
            f"current_price must be float or None, got {type(pos['current_price'])}"
        )
        print(f"OK: list returns 1 position; current_price={pos['current_price']!r}")

        # --- Step 5: delete ---
        delete_user_portfolio_position(TEST_USER_ID, TEST_TICKER)
        positions = list_user_portfolio_positions(TEST_USER_ID)
        assert len(positions) == 0, f"Expected 0 positions after delete, got {len(positions)}"
        print("OK: delete — position removed")

    finally:
        # Cleanup any orphan test rows
        client.query(
            f"DELETE FROM `{_table_ref(client, _USER_PORTFOLIO_POSITIONS_TABLE_NAME)}`"
            f" WHERE user_id = '{TEST_USER_ID}'"
        ).result()
        print("OK: sentinel rows cleaned up")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
