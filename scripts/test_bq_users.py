"""Round-trip smoke test for Phase 2 (PUL-71) BQ users table against real BigQuery.

Verifies:
  2.a  users table created by create_users_table_if_not_exists + schema migrated
  2.b  insert_user lands a row with created_at set and last_login_at NULL
  2.c  upsert_user_login bumps last_login_at on the existing row (MATCHED path)
  2.d  upsert_user_login self-heals a missing row (NOT MATCHED path)

Run with:
    uv run python scripts/test_bq_users.py

Requires ADC: gcloud auth application-default login
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from google.cloud import bigquery

from db.bigquery import (
    _get_client,
    _table_ref,
    _USERS_TABLE_NAME,
    create_users_table_if_not_exists,
    ensure_users_schema_current,
    insert_user,
    upsert_user_login,
)

TEST_USER_ID = "e2e-pul71-test-user"
TEST_USER_ID_HEAL = "e2e-pul71-test-user-heal"
TEST_EMAIL = "pul71-roundtrip@example.com"


def _fetch_user(client, user_id: str) -> dict | None:
    query = f"""
        SELECT user_id, email, created_at, last_login_at
        FROM `{_table_ref(client, _USERS_TABLE_NAME)}`
        WHERE user_id = @user_id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("user_id", "STRING", user_id)]
    )
    rows = list(client.query(query, job_config=job_config).result())
    assert len(rows) <= 1, f"FAIL: duplicate rows for {user_id}"
    if not rows:
        return None
    r = rows[0]
    return {
        "user_id": r.user_id,
        "email": r.email,
        "created_at": r.created_at,
        "last_login_at": r.last_login_at,
    }


def _cleanup(client) -> None:
    query = f"""
        DELETE FROM `{_table_ref(client, _USERS_TABLE_NAME)}`
        WHERE user_id IN (@u1, @u2)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("u1", "STRING", TEST_USER_ID),
            bigquery.ScalarQueryParameter("u2", "STRING", TEST_USER_ID_HEAL),
        ]
    )
    client.query(query, job_config=job_config).result()
    print("    Cleanup: test rows deleted")


def main() -> None:
    client = _get_client()

    # --- 2.a: table creation + schema migration ---
    print("=== 2.a: create users table + ensure schema ===")
    create_users_table_if_not_exists()
    ensure_users_schema_current()
    table = client.get_table(_table_ref(client, _USERS_TABLE_NAME))
    cols = [f.name for f in table.schema]
    print(f"    Columns: {cols}")
    expected = {"user_id", "email", "created_at", "last_login_at"}
    missing = expected - set(cols)
    assert not missing, f"FAIL: missing columns: {missing}"
    print("    OK: users table exists with correct schema")

    try:
        # --- 2.b: insert_user ---
        print("=== 2.b: insert_user ===")
        insert_user(TEST_USER_ID, TEST_EMAIL)
        row = _fetch_user(client, TEST_USER_ID)
        print(f"    Row: {row}")
        assert row is not None, "FAIL: row not found after insert_user"
        assert row["email"] == TEST_EMAIL
        assert row["created_at"] is not None, "FAIL: created_at is NULL"
        assert row["last_login_at"] is None, "FAIL: last_login_at should be NULL after insert"
        print("    OK: row inserted, created_at set, last_login_at NULL")

        # --- 2.c: upsert_user_login on existing row (MATCHED) ---
        print("=== 2.c: upsert_user_login MATCHED ===")
        upsert_user_login(TEST_USER_ID, TEST_EMAIL)
        row2 = _fetch_user(client, TEST_USER_ID)
        print(f"    Row: {row2}")
        assert row2 is not None
        assert row2["last_login_at"] is not None, "FAIL: last_login_at not set by upsert"
        assert row2["created_at"] == row["created_at"], "FAIL: created_at changed on login"
        print("    OK: last_login_at set, created_at untouched")

        # --- 2.d: upsert_user_login self-heal (NOT MATCHED) ---
        print("=== 2.d: upsert_user_login NOT MATCHED (self-heal) ===")
        upsert_user_login(TEST_USER_ID_HEAL, TEST_EMAIL)
        row3 = _fetch_user(client, TEST_USER_ID_HEAL)
        print(f"    Row: {row3}")
        assert row3 is not None, "FAIL: self-heal did not insert a row"
        assert row3["created_at"] is not None and row3["last_login_at"] is not None
        print("    OK: missing row self-healed with created_at + last_login_at")
    finally:
        _cleanup(client)

    print("\nAll Phase 2 manual checks PASSED.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
