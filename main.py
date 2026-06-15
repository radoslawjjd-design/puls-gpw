import argparse
import logging
import sys

from dotenv import load_dotenv

load_dotenv()

from src.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

from db.bigquery import (
    BigQueryError,
    announcement_id_for_url,
    create_table_if_not_exists,
    create_x_posts_table_if_not_exists,
    ensure_schema_current,
    insert_announcement,
    save_analysis_result,
    update_parsed_content,
)
from src.analyzer import analyze_announcement
from src.notifier import send_alert
from src.parser import parse_announcement
from src.scraper import scrape_new_announcements


def main():
    parser = argparse.ArgumentParser(description="ESPI/EBI announcement pipeline")
    parser.add_argument("--hours", type=int, default=None, metavar="N",
                        help="Scrape window in hours (overrides SCRAPE_WINDOW_MINUTES)")
    parser.add_argument("--max-pages", type=int, default=None, metavar="N",
                        help="Max Bankier pages (overrides MAX_PAGES_BANKIER)")
    args = parser.parse_args()

    window_minutes = args.hours * 60 if args.hours else None

    try:
        create_table_if_not_exists()
        ensure_schema_current()
        create_x_posts_table_if_not_exists()
        new = scrape_new_announcements(window_minutes=window_minutes, max_pages=args.max_pages)
        if not new:
            logger.info("Pipeline completed: 0 new announcements")
            return
        stored = 0
        skipped_no_ticker = 0
        for ann in new:
            try:
                # Parse BEFORE storing so we can resolve the ticker first. announcement_id
                # is a deterministic hash of the URL, so it's stable without an insert.
                ann_id = announcement_id_for_url(ann.bankier_url)
                parsed = parse_announcement(ann, ann_id)
                if not parsed.ticker:
                    # No resolvable ticker → not a tradable company (likely an ETF/fund or
                    # an issuer-less report). We don't want these in the DB at all — skip
                    # without inserting. Not deduped, so a later run re-checks it.
                    skipped_no_ticker += 1
                    logger.info(
                        "Pipeline: skipping %s — no ticker resolved (likely ETF/fund); not stored",
                        ann.bankier_url,
                    )
                    continue
                insert_announcement(ann.bankier_url, ann.published_at, ann.title, ann.priority)
                update_parsed_content(ann_id, parsed.parsed_content, parsed.ticker, parsed.company)
                result = analyze_announcement(ann_id, parsed.parsed_content, parsed.ticker, ann.priority)
                try:
                    save_analysis_result(
                        ann_id,
                        result.structured_analysis,
                        result.analysis_approved,
                        result.analysis_reject_reason,
                        result.event_type,
                        result.analysis_score,
                    )
                    if result.analysis_approved is False:
                        logger.warning(
                            "Analyzer: rejected %s — %s", ann_id, result.analysis_reject_reason
                        )
                except BigQueryError:
                    # best-effort: analysis save failure doesn't block the run;
                    # the row stays with analyzed_at=NULL and won't enter the post window.
                    logger.warning("BQ save_analysis_result failed for %s — skipping", ann_id)
                stored += 1
            except BigQueryError:
                raise  # propagate to outer except → send_alert
            except Exception:
                logger.exception("Unexpected error processing %s — skipping", ann.bankier_url)
        logger.info(
            "Pipeline completed: %d stored, %d skipped (no ticker), %d scraped",
            stored, skipped_no_ticker, len(new),
        )
    except Exception as exc:
        logger.exception("Pipeline failed")
        try:
            send_alert(exc)
            logger.info("Alert email sent")
        except Exception as alert_exc:
            logger.error("Failed to send alert: %s", alert_exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
