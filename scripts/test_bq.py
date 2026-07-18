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
    _X_POSTS_TABLE_NAME,
    _get_client,
    _table_ref,
    add_watchlist_ticker,
    create_table_if_not_exists,
    create_watchlist_table_if_not_exists,
    create_x_posts_table_if_not_exists,
    ensure_schema_current,
    ensure_watchlist_schema_current,
    ensure_x_posts_schema_current,
    insert_announcement,
    is_processed,
    list_watchlist_tickers,
    remove_watchlist_ticker,
    save_analysis_result,
    save_x_post,
    update_x_post_publish_result,
    x_post_already_published,
)

TEST_WATCHLIST_USER_ID = "test-bq-integration-watchlist-client"
TEST_WATCHLIST_TICKER = "TST"

TEST_URL = "https://www.bankier.pl/gielda/wiadomosci/komunikaty-spolek/test-bq-integration-F02"


def main() -> None:
    # Step 1 — ensure tables exist
    create_table_if_not_exists()
    ensure_schema_current()  # migrates announcements.x_post_id onto the existing table
    create_x_posts_table_if_not_exists()
    ensure_x_posts_schema_current()  # migrates x_posts.x_publish_status onto the existing table
    create_watchlist_table_if_not_exists()
    ensure_watchlist_schema_current()
    client = _get_client()
    table = _table_ref(client)
    x_posts_table = _table_ref(client, _X_POSTS_TABLE_NAME)
    print(f"[1] Tables ready: {table}, {x_posts_table}")

    # Step 2 — insert test announcement
    ann_id = insert_announcement(
        url=TEST_URL,
        published_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        title="Test announcement F-02",
    )
    print(f"[2] Inserted announcement_id: {ann_id}")

    x_post_id = None
    try:
        # Step 3 — dedup check
        processed = is_processed(TEST_URL)
        assert processed, "is_processed should return True after insert"
        print(f"[3] is_processed: {processed}")

        # Step 4 — save analysis result
        save_analysis_result(
            announcement_id=ann_id,
            structured_analysis='{"company": "Test SA", "ticker": "TST", "event_type": "wyniki_finansowe", "key_numbers": [], "sentiment": "pozytywny", "summary_pl": "Test."}',
            analysis_approved=True,
            analysis_reject_reason=None,
            event_type="wyniki_finansowe",
            analysis_score=125.0,
        )
        print("[4] save_analysis_result: OK")

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
            f"analysis_approved={row.analysis_approved!r} event_type={row.event_type!r} "
            f"analysis_score={row.analysis_score!r} analyzed_at={row.analyzed_at!r}"
        )

        # Step 6 — save_x_post round-trip: insert x_posts row + link announcement
        x_post_id = save_x_post([ann_id], "tweet1\n\ntweet2", "poludnie", 1)
        print(f"[6] save_x_post returned x_post_id: {x_post_id}")

        # Step 7 — read back the x_posts row
        x_rows = list(
            client.query(
                f"SELECT * FROM `{x_posts_table}` WHERE x_post_id = @xid",
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[bigquery.ScalarQueryParameter("xid", "STRING", x_post_id)]
                ),
            ).result()
        )
        assert x_rows, "Expected one x_posts row after save_x_post"
        x_row = x_rows[0]
        assert x_row.window == "poludnie", f"window mismatch: {x_row.window!r}"
        assert x_row.post_text == "tweet1\n\ntweet2", f"post_text mismatch: {x_row.post_text!r}"
        assert x_row.supervisor_attempts == 1, f"attempts mismatch: {x_row.supervisor_attempts!r}"
        assert x_row.posted_at is not None, "posted_at should be stamped"
        print(
            f"[7] x_posts row: window={x_row.window!r} post_text={x_row.post_text!r} "
            f"supervisor_attempts={x_row.supervisor_attempts!r} posted_at={x_row.posted_at!r}"
        )

        # Step 8 — confirm announcement was linked
        link_rows = list(
            client.query(
                f"SELECT x_post_id FROM `{table}` WHERE announcement_id = @id",
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", ann_id)]
                ),
            ).result()
        )
        assert link_rows and link_rows[0].x_post_id == x_post_id, (
            f"announcement x_post_id not linked: {link_rows!r}"
        )
        print(f"[8] announcement linked: x_post_id={link_rows[0].x_post_id!r}")

        # Step 10 — publish-result write: tweet_ids + x_publish_status onto the row
        update_x_post_publish_result(x_post_id, ["111111", "222222"], "published")
        pub_rows = list(
            client.query(
                f"SELECT tweet_ids, x_publish_status FROM `{x_posts_table}` WHERE x_post_id = @xid",
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[bigquery.ScalarQueryParameter("xid", "STRING", x_post_id)]
                ),
            ).result()
        )
        assert pub_rows, "Expected the x_posts row after update_x_post_publish_result"
        pub_row = pub_rows[0]
        assert pub_row.tweet_ids == "111111,222222", f"tweet_ids mismatch: {pub_row.tweet_ids!r}"
        assert pub_row.x_publish_status == "published", (
            f"x_publish_status mismatch: {pub_row.x_publish_status!r}"
        )
        print(
            f"[10] publish result: tweet_ids={pub_row.tweet_ids!r} "
            f"x_publish_status={pub_row.x_publish_status!r}"
        )

        # Step 11 — idempotency guard sees the just-published "poludnie" row today
        already = x_post_already_published("poludnie")
        assert already is True, "x_post_already_published should be True after publish"
        print(f"[11] idempotency guard (poludnie, today): already_published={already}")

        # Step 12 — watchlist round-trip: add, list, remove, confirm empty
        add_watchlist_ticker(TEST_WATCHLIST_USER_ID, TEST_WATCHLIST_TICKER)
        tickers = list_watchlist_tickers(TEST_WATCHLIST_USER_ID)
        assert tickers == [TEST_WATCHLIST_TICKER], f"expected one watchlisted ticker, got {tickers!r}"
        print(f"[12] watchlist add+list: {tickers}")

        add_watchlist_ticker(TEST_WATCHLIST_USER_ID, TEST_WATCHLIST_TICKER)
        tickers_after_dup = list_watchlist_tickers(TEST_WATCHLIST_USER_ID)
        assert tickers_after_dup == [TEST_WATCHLIST_TICKER], (
            f"duplicate add must be a no-op, got {tickers_after_dup!r}"
        )
        print(f"[12] watchlist duplicate add is a no-op: {tickers_after_dup}")

        remove_watchlist_ticker(TEST_WATCHLIST_USER_ID, TEST_WATCHLIST_TICKER)
        tickers_after_remove = list_watchlist_tickers(TEST_WATCHLIST_USER_ID)
        assert tickers_after_remove == [], f"expected empty watchlist after remove, got {tickers_after_remove!r}"
        print("[12] watchlist remove: list is empty again")
    finally:
        # Step 9 — cleanup (runs even on error to avoid orphaned test records)
        try:
            client.query(
                f"DELETE FROM `{table}` WHERE announcement_id = @id",
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", ann_id)]
                ),
            ).result()
            print("[9] Cleanup: test announcement deleted")
        except Exception as exc:
            print(f"[9] WARNING: cleanup failed ({exc}) — delete test record manually: {ann_id}")
        if x_post_id is not None:
            try:
                client.query(
                    f"DELETE FROM `{x_posts_table}` WHERE x_post_id = @xid",
                    job_config=bigquery.QueryJobConfig(
                        query_parameters=[bigquery.ScalarQueryParameter("xid", "STRING", x_post_id)]
                    ),
                ).result()
                print("[9] Cleanup: test x_posts row deleted")
            except Exception as exc:
                print(f"[9] WARNING: x_posts cleanup failed ({exc}) — delete manually: {x_post_id}")
        try:
            remove_watchlist_ticker(TEST_WATCHLIST_USER_ID, TEST_WATCHLIST_TICKER)
            print("[12] Cleanup: test watchlist row removed (no-op if already removed)")
        except Exception as exc:
            print(f"[12] WARNING: watchlist cleanup failed ({exc}) — remove manually: {TEST_WATCHLIST_USER_ID}")

    print("\nAll steps passed.")


if __name__ == "__main__":
    main()
