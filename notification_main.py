"""Cloud Run Job entrypoint for the watchlist email-notification delivery pass.

Runs every ~5 min (24/7): finds new analyzed announcements of watched companies,
joins enabled notification subscriptions, and emails each opted-in user a single
digest of their new announcements — deduplicated per (user_id, announcement_id)
via the sent-log so nobody is emailed the same announcement twice.
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

from src.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

from db.bigquery import (
    create_notification_sent_log_table_if_not_exists,
    ensure_notification_sent_log_schema_current,
    record_notification_sent,
    select_pending_notifications,
)
from src.notifier import send_alert, send_announcement_digest_email

# Candidate pre-filter window: bounds the join cheaply via the published_at DAY
# partition. The sent-log anti-join guarantees exactly-once regardless, so this
# only needs to comfortably exceed scrape+analyze latency.
_CANDIDATE_WINDOW = timedelta(hours=48)


def _group_by_user(rows: list[dict]) -> "dict[str, dict]":
    """Group recipient rows into {user_id: {"email": ..., "items": [...]}}."""
    grouped: dict[str, dict] = {}
    for row in rows:
        entry = grouped.setdefault(row["user_id"], {"email": row.get("email"), "items": []})
        entry["items"].append(row)
    return grouped


def main() -> None:
    parser = argparse.ArgumentParser(description="Watchlist email-notification delivery pass")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="log recipients but do not send emails or record the sent-log",
    )
    args = parser.parse_args()

    base_url = os.environ.get("APP_BASE_URL", "https://puls-gpw-api-5zlombicra-lm.a.run.app")

    try:
        # Jobs do not go through the API startup hook — bootstrap our own tables.
        create_notification_sent_log_table_if_not_exists()
        ensure_notification_sent_log_schema_current()

        cutoff = datetime.now(timezone.utc) - _CANDIDATE_WINDOW
        rows = select_pending_notifications(cutoff)
        if not rows:
            logger.info("notification_main: no pending notifications")
            return

        by_user = _group_by_user(rows)
        logger.info(
            "notification_main: %d pending pair(s) across %d user(s)%s",
            len(rows), len(by_user), " [dry-run]" if args.dry_run else "",
        )

        failures = 0
        for user_id, entry in by_user.items():
            email, items = entry["email"], entry["items"]
            # F2: everything inside the per-user block is isolated — a send OR
            # record failure for one user must not block the others. The pair is
            # left un-recorded so the next pass retries it.
            try:
                if args.dry_run:
                    logger.info("notification_main: [dry-run] would email %s (%d item(s))", email, len(items))
                    continue
                send_announcement_digest_email(email, items, base_url)
                for item in items:
                    record_notification_sent(user_id, item["announcement_id"], email)
            except Exception:
                failures += 1
                logger.exception("notification_main: delivery failed for user_id=%s", user_id)
                continue

        logger.info(
            "notification_main: done — users=%d failures=%d%s",
            len(by_user), failures, " [dry-run]" if args.dry_run else "",
        )
        if failures:
            try:
                send_alert(RuntimeError(f"notification delivery: {failures} recipient(s) failed"))
            except Exception as alert_exc:
                logger.error("notification_main: failed to send failure alert: %s", alert_exc)

    except Exception as exc:
        # Fatal: table bootstrap or the recipient query failed — the pass can't run.
        logger.exception("notification_main: pipeline failed")
        try:
            send_alert(exc)
            logger.info("notification_main: alert email sent")
        except Exception as alert_exc:
            logger.error("notification_main: failed to send alert: %s", alert_exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
