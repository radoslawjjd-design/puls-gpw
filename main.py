import argparse
import logging
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from src.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

from db.bigquery import (
    BigQueryError,
    announcement_id_for_url,
    create_companies_table_if_not_exists,
    create_notification_sent_log_table_if_not_exists,
    create_table_if_not_exists,
    create_x_posts_table_if_not_exists,
    ensure_companies_schema_current,
    ensure_notification_sent_log_schema_current,
    ensure_schema_current,
    insert_announcement,
    record_notification_sent,
    save_analysis_result,
    select_recipients_for_announcement,
    update_parsed_content,
    upsert_company,
)
from src.analyzer import analyze_announcement
from src.notifier import send_alert, send_announcement_digest_email
from src.parser import parse_announcement
from src.scraper import scrape_new_announcements

# The scraper job now emails opted-in watchers inline (event-driven, PUL-81 v2).
# The link/logo in the mail need the app's public base URL; the scraper job
# carries APP_BASE_URL as env (the run.app web URL, NOT the SMTP-From domain).
_DEFAULT_BASE_URL = "https://puls-gpw-api-5zlombicra-lm.a.run.app"
# Per-recipient send retries (in-run only — there is no next pass; F1).
_NOTIF_SEND_ATTEMPTS = 3
_NOTIF_SEND_BACKOFF_S = 2.0


def _notify_recipient(recipient: dict, item: dict, ann_id: str, base_url: str) -> int:
    """Email one watcher their single-announcement digest, then record it.

    Retries the send in-run (2-3 attempts, short backoff) — there is NO next pass
    (the scraper skips already-processed announcements, F1), so an exhausted send
    is permanently missed. Returns 1 when the recipient is missed (send exhausted
    or the dedup record failed), else 0. Never raises — the caller stays isolated.
    """
    email = recipient.get("email")
    user_id = recipient.get("user_id")
    for attempt in range(_NOTIF_SEND_ATTEMPTS):
        try:
            send_announcement_digest_email(email, [item], base_url)
            break
        except Exception:
            if attempt + 1 < _NOTIF_SEND_ATTEMPTS:
                time.sleep(_NOTIF_SEND_BACKOFF_S)
            else:
                logger.exception(
                    "Notification permanently missed for user=%s ann=%s (send failed x%d)",
                    user_id, ann_id, _NOTIF_SEND_ATTEMPTS,
                )
                return 1
    try:
        record_notification_sent(user_id, ann_id, email)
    except Exception:
        # The email went out; only the idempotent dedup record failed. Flag it so
        # the closing alert notes it (a re-process of this exact announcement could
        # re-send — normally it isn't re-processed).
        logger.exception("record_notification_sent failed for user=%s ann=%s", user_id, ann_id)
        return 1
    return 0


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
        create_companies_table_if_not_exists()
        ensure_companies_schema_current()
        # Jobs bootstrap their own tables — the notification hook writes the sent-log.
        create_notification_sent_log_table_if_not_exists()
        ensure_notification_sent_log_schema_current()
        new = scrape_new_announcements(window_minutes=window_minutes, max_pages=args.max_pages)
        if not new:
            logger.info("Pipeline completed: 0 new announcements")
            return
        base_url = os.environ.get("APP_BASE_URL", _DEFAULT_BASE_URL)
        stored = 0
        skipped_no_ticker = 0
        notif_failures = 0
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
                try:
                    upsert_company(parsed.ticker, parsed.company, parsed.hop_url, parsed.isin)
                except BigQueryError:
                    # best-effort: dictionary enrichment is lower-stakes than the core
                    # pipeline — don't let it block analysis/alerting.
                    logger.warning("BQ upsert_company failed for %s — skipping", parsed.ticker)
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
                # Event-driven notification hook (PUL-81 v2): email opted-in watchers
                # inline, the moment the announcement is analyzed + saved. FULLY
                # ISOLATED — no failure (recipient query, SMTP, record) may escape to
                # abort ingestion; a failure only bumps notif_failures for a closing
                # owner alert. Stricter than the core steps, where BigQueryError
                # intentionally propagates.
                if result.analysis_approved is True and result.analysis_score is not None:
                    try:
                        recipients = select_recipients_for_announcement(ann_id)
                        item = {
                            "company": parsed.company,
                            "ticker": parsed.ticker,
                            "title": ann.title,
                            "event_type": result.event_type,
                        }
                        for r in recipients:
                            notif_failures += _notify_recipient(r, item, ann_id, base_url)
                    except Exception:
                        # incl. BigQueryError from the recipient query — contained here
                        # so it never reaches the per-item `except BigQueryError: raise`.
                        logger.exception(
                            "Notification hook failed for %s — ingestion continues", ann_id
                        )
                        notif_failures += 1
                stored += 1
            except BigQueryError:
                raise  # propagate to outer except → send_alert
            except Exception:
                logger.exception("Unexpected error processing %s — skipping", ann.bankier_url)
        logger.info(
            "Pipeline completed: %d stored, %d skipped (no ticker), %d scraped",
            stored, skipped_no_ticker, len(new),
        )
        if notif_failures:
            # Notifications are low-stakes and NEVER abort ingestion — surface the
            # miss to the owner once (do NOT sys.exit).
            logger.warning("Notification hook: %d send(s) failed this run", notif_failures)
            try:
                send_alert(RuntimeError(
                    f"{notif_failures} watchlist notification(s) failed to send this run "
                    "(permanently missed — no next pass)."
                ))
            except Exception as alert_exc:
                logger.error("Failed to send notification-failure alert: %s", alert_exc)
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
