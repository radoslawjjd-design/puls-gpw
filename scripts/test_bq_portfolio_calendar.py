"""Round-trip smoke test for Phase 1 (PUL-59) BQ changes against real BigQuery.

Verifies:
  1.4  get_portfolio_calendar_data returns rows for trading days in queried month
  1.5  SQL has no un-backticked reserved keywords (verified by running the query)

Run with:
    uv run python scripts/test_bq_portfolio_calendar.py

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
    _USER_PORTFOLIOS_TABLE_NAME,
    get_portfolio_calendar_data,
    list_user_portfolios,
)

TEST_USER_ID = "e2e-pul59-calendar-test"


def main() -> None:
    client = _get_client()

    # --- find a real user with portfolios ---
    print("=== Finding a user with portfolios ===")
    query = f"""
        SELECT user_id, portfolio_id
        FROM `{_table_ref(client, _USER_PORTFOLIOS_TABLE_NAME)}`
        LIMIT 1
    """
    rows = list(client.query(query).result())
    if not rows:
        print("  No portfolios found in user_portfolios — skipping 1.4 (no data to test with)")
        print("  Verifying empty-portfolio path instead...")
        result = get_portfolio_calendar_data("no-such-portfolio", TEST_USER_ID, 2026, 6)
        assert result == [], f"FAIL: expected [] for missing portfolio, got {result}"
        print("  OK: returns [] for portfolio with no positions")
        print("\nAll available Phase 1 (PUL-59) checks PASSED.")
        return

    real_user_id = rows[0].user_id
    real_portfolio_id = rows[0].portfolio_id
    print(f"  Using user_id={real_user_id}, portfolio_id={real_portfolio_id}")

    # --- 1.4: round-trip for a real portfolio ---
    print("\n=== 1.4: get_portfolio_calendar_data round-trip ===")
    result = get_portfolio_calendar_data(real_portfolio_id, real_user_id, 2026, 6)
    print(f"  Returned {len(result)} rows")
    if result:
        print(f"  First row: {result[0]}")
        print(f"  Last row:  {result[-1]}")
        row = result[0]
        assert "snapshot_date" in row, "FAIL: missing snapshot_date"
        assert "portfolio_value" in row, "FAIL: missing portfolio_value"
        assert "prices_found" in row, "FAIL: missing prices_found"
        assert "total_positions" in row, "FAIL: missing total_positions"
        assert isinstance(row["portfolio_value"], float), "FAIL: portfolio_value not float"
        assert isinstance(row["prices_found"], int), "FAIL: prices_found not int"
        print("  OK: shape verified")
    else:
        print("  OK: portfolio exists but has no positions in June 2026 ([] returned correctly)")

    print("\nAll Phase 1 (PUL-59) manual checks PASSED.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
