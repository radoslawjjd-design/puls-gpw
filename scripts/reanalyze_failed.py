"""Re-analyze announcements whose Gemini analysis failed (analysis_score IS NULL).

Finds all rows where analyzed_at IS NOT NULL (already attempted) but analysis_score
IS NULL (failed, typically due to 429 RESOURCE_EXHAUSTED), and re-runs the full
analysis pipeline for each.

Run with:
    uv run python scripts/reanalyze_failed.py [--date YYYY-MM-DD] [--dry-run]

--date   process only this date (default: today in Warsaw time)
--dry-run  print matching rows, do not call Gemini or update BQ
"""
import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

from db.bigquery import BigQueryError, _get_client, _table_ref, save_analysis_result
from src.analyzer import analyze_announcement

_BETWEEN_CALLS_S = 0  # no artificial delay — global endpoint has no observed QPM limit


def _fetch_failed(target_date: date) -> list[dict]:
    client = _get_client()
    query = f"""
        SELECT
            announcement_id,
            parsed_content,
            ticker,
            priority
        FROM `{_table_ref(client)}`
        WHERE
            DATE(published_at, 'Europe/Warsaw') = @target_date
            AND analyzed_at IS NOT NULL
            AND analysis_score IS NULL
            AND parsed_content IS NOT NULL
            AND ticker IS NOT NULL
        ORDER BY published_at
    """
    from google.cloud import bigquery
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("target_date", "DATE", target_date),
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"_fetch_failed query failed: {exc}") from exc
    return [
        {
            "announcement_id": r.announcement_id,
            "parsed_content": r.parsed_content,
            "ticker": r.ticker,
            "priority": r.priority,
        }
        for r in rows
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-analyze failed announcements")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today Warsaw)")
    parser.add_argument("--dry-run", action="store_true", help="Print rows, skip Gemini calls")
    args = parser.parse_args()

    if args.date:
        target_date = date.fromisoformat(args.date)
    else:
        from datetime import timezone, timedelta
        WARSAW = timezone(timedelta(hours=2))
        from datetime import datetime
        target_date = datetime.now(WARSAW).date()

    logger.info("Fetching failed analyses for %s …", target_date)
    rows = _fetch_failed(target_date)
    logger.info("Found %d rows to re-analyze", len(rows))

    if not rows:
        logger.info("Nothing to do.")
        return

    if args.dry_run:
        for r in rows:
            print(f"  {r['announcement_id'][:16]}…  ticker={r['ticker']}  priority={r['priority']}")
        return

    ok = 0
    failed = 0
    for i, row in enumerate(rows, 1):
        ann_id = row["announcement_id"]
        logger.info("[%d/%d] Analyzing %s (ticker=%s) …", i, len(rows), ann_id[:16], row["ticker"])
        result = analyze_announcement(
            ann_id, row["parsed_content"], row["ticker"], row["priority"]
        )
        if result.analysis_score is None:
            logger.warning("[%d/%d] Still no score for %s — skipping BQ update", i, len(rows), ann_id[:16])
            failed += 1
        else:
            try:
                save_analysis_result(
                    ann_id,
                    result.structured_analysis,
                    result.analysis_approved,
                    result.analysis_reject_reason,
                    result.event_type,
                    result.analysis_score,
                )
                logger.info("[%d/%d] Saved: event_type=%s score=%s", i, len(rows), result.event_type, result.analysis_score)
                ok += 1
            except BigQueryError as exc:
                logger.error("[%d/%d] BQ save failed for %s: %s", i, len(rows), ann_id[:16], exc)
                failed += 1

        if i < len(rows) and _BETWEEN_CALLS_S > 0:
            time.sleep(_BETWEEN_CALLS_S)

    logger.info("Done: %d OK, %d failed (out of %d)", ok, failed, len(rows))


if __name__ == "__main__":
    main()
