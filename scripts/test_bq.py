"""End-to-end round-trip test for db.bigquery wrappers against a real BigQuery dataset.

Run with:
    uv run python scripts/test_bq.py

Requires ADC: gcloud auth application-default login
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from datetime import datetime, timezone

from google.cloud import bigquery

from db.bigquery import (
    _get_client,
    _table_ref,
    create_table_if_not_exists,
    insert_announcement,
    is_processed,
    save_analysis,
)

TEST_URL = "https://www.bankier.pl/gielda/wiadomosci/komunikaty-spolek/test-bq-integration-F02"


def main() -> None:
    # Step 1 — ensure table exists
    create_table_if_not_exists()
    client = _get_client()
    table = _table_ref(client)
    print(f"[1] Table ready: {table}")

    # Step 2 — insert test announcement
    ann_id = insert_announcement(
        url=TEST_URL,
        published_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        title="Test announcement F-02",
        company="Test Spółka S.A.",
        ticker="TST",
    )
    print(f"[2] Inserted announcement_id: {ann_id}")

    try:
        # Step 3 — dedup check
        processed = is_processed(TEST_URL)
        assert processed, "is_processed should return True after insert"
        print(f"[3] is_processed: {processed}")

        # Step 4 — save analysis
        save_analysis(
            announcement_id=ann_id,
            post_text="Test post #TST wyniki finansowe.",
            analysis_type="FINANCIAL",
            supervisor_attempts=1,
        )
        print("[4] save_analysis: OK")

        # Step 5 — read back and display
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", ann_id)]
        )
        rows = list(
            client.query(
                f"SELECT * FROM `{table}` WHERE announcement_id = @id",
                job_config=job_config,
            ).result()
        )
        assert rows, "Expected exactly one row after insert+update"
        row = rows[0]
        print(
            f"[5] Record: url={row.url!r} title={row.title!r} "
            f"analysis_type={row.analysis_type!r} post_text={row.post_text!r} "
            f"supervisor_attempts={row.supervisor_attempts}"
        )
    finally:
        # Step 6 — cleanup (runs even on error to avoid orphaned test records)
        client.query(
            f"DELETE FROM `{table}` WHERE announcement_id = @id",
            job_config=bigquery.QueryJobConfig(
                query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", ann_id)]
            ),
        ).result()
        print("[6] Cleanup: test record deleted")

    print("\nAll steps passed.")


if __name__ == "__main__":
    main()
