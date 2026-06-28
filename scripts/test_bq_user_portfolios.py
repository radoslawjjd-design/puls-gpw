"""Round-trip smoke test for Phase 1 (PUL-64) BQ changes against real BigQuery.

Verifies:
  1.4  portfolio_id column exists in user_portfolio_positions after ensure_schema_current
  1.5  user_portfolios table created by create_user_portfolios_table_if_not_exists
  1.6  list_user_portfolios returns [] for a new user

Run with:
    uv run python scripts/test_bq_user_portfolios.py

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
    _USER_PORTFOLIOS_TABLE_NAME,
    create_user_portfolio_positions_table_if_not_exists,
    create_user_portfolios_table_if_not_exists,
    ensure_user_portfolio_positions_schema_current,
    ensure_user_portfolios_schema_current,
    list_user_portfolios,
)

TEST_USER_ID = "e2e-pul64-test-user"


def _get_columns(client, table_name: str) -> list[str]:
    table = client.get_table(_table_ref(client, table_name))
    return [f.name for f in table.schema]


def main() -> None:
    client = _get_client()

    # --- 1.4: portfolio_id column in user_portfolio_positions ---
    print("=== 1.4: ensure portfolio_id column in user_portfolio_positions ===")
    create_user_portfolio_positions_table_if_not_exists()
    ensure_user_portfolio_positions_schema_current()
    cols = _get_columns(client, _USER_PORTFOLIO_POSITIONS_TABLE_NAME)
    print(f"    Columns: {cols}")
    assert "portfolio_id" in cols, f"FAIL: portfolio_id not in columns: {cols}"
    print("    OK: portfolio_id column present (existing rows have NULL)")

    # --- 1.5: user_portfolios table creation ---
    print("=== 1.5: create user_portfolios table ===")
    create_user_portfolios_table_if_not_exists()
    ensure_user_portfolios_schema_current()
    cols = _get_columns(client, _USER_PORTFOLIOS_TABLE_NAME)
    print(f"    Columns: {cols}")
    expected = {"user_id", "portfolio_id", "portfolio_type", "portfolio_name", "display_order", "created_at"}
    missing = expected - set(cols)
    assert not missing, f"FAIL: missing columns: {missing}"
    print("    OK: user_portfolios table exists with correct schema")

    # --- 1.6: list_user_portfolios returns [] for new user ---
    print("=== 1.6: list_user_portfolios for new user ===")
    portfolios = list_user_portfolios(TEST_USER_ID)
    print(f"    Result: {portfolios}")
    assert portfolios == [], f"FAIL: expected [], got {portfolios}"
    print("    OK: returns [] for new user")

    print("\nAll Phase 1 manual checks PASSED.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
