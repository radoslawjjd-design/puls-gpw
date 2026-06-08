"""Cloud Run Job entrypoint for the X post generation pipeline."""
import argparse
import logging
import sys
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from src.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

from db.bigquery import fetch_top_n_for_window, save_post_text
from src.notifier import send_alert, send_no_post_email, send_post_email
from src.post_generator import generate_post
from src.post_supervisor import validate_post

WARSAW = ZoneInfo("Europe/Warsaw")

_WINDOW_NAMES = {
    "ranek": "Ranek",
    "poludnie": "Południe",
    "wieczor": "Wieczór",
}


def _detect_window(now_warsaw: datetime) -> str | None:
    t = now_warsaw.time()
    if t <= time(8, 30):
        return "ranek"
    if t <= time(13, 0):
        return "poludnie"
    if t <= time(17, 30):
        return "wieczor"
    return None


def _window_bounds(window: str, now_warsaw: datetime) -> tuple[datetime, datetime]:
    today = now_warsaw.date()
    if window == "ranek":
        # Spans midnight: previous day 17:31 → today 08:29 (DST-safe yesterday)
        yesterday = (now_warsaw - timedelta(hours=15)).date()
        start = datetime(yesterday.year, yesterday.month, yesterday.day, 17, 31, tzinfo=WARSAW)
        end = datetime(today.year, today.month, today.day, 8, 29, tzinfo=WARSAW)
    elif window == "poludnie":
        start = datetime(today.year, today.month, today.day, 8, 30, tzinfo=WARSAW)
        end = datetime(today.year, today.month, today.day, 12, 59, tzinfo=WARSAW)
    else:  # wieczor
        start = datetime(today.year, today.month, today.day, 13, 0, tzinfo=WARSAW)
        end = datetime(today.year, today.month, today.day, 17, 29, tzinfo=WARSAW)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser(description="X post generation pipeline")
    parser.add_argument(
        "--window",
        choices=["ranek", "poludnie", "wieczor"],
        default=None,
        help="Time window to process (default: auto-detect from current Warsaw time)",
    )
    args = parser.parse_args()

    now_warsaw = datetime.now(WARSAW)
    date_str = now_warsaw.strftime("%d.%m.%Y")

    window = args.window or _detect_window(now_warsaw)
    if window is None:
        logger.warning("post_main: no active window at %s Warsaw — exiting", now_warsaw.time())
        sys.exit(0)

    window_name = _WINDOW_NAMES[window]
    logger.info("post_main: processing window=%s date=%s", window, date_str)

    try:
        window_start, window_end = _window_bounds(window, now_warsaw)
        announcements = fetch_top_n_for_window(window_start, window_end, n=4)

        ann_ids = [a["announcement_id"] for a in announcements]
        tickers = list(dict.fromkeys(a["ticker"] for a in announcements if a.get("ticker")))

        if not tickers:
            logger.info("post_main: no valid-ticker announcements for %s — skipping", window)
            if window != "poludnie":
                send_no_post_email(window_name, date_str, "Brak zatwierdzonych ogłoszeń w oknie.")
            return
        expected_tweets = len(tickers) + 2

        post = None
        for attempt in range(1, 4):
            post = generate_post(announcements)
            if post is None:
                logger.warning("post_main: generate_post returned None on attempt %d", attempt)
                continue
            result = validate_post(post, tickers, expected_tweets=expected_tweets)
            if result.approved:
                save_post_text(ann_ids, "\n\n".join(post.tweets), attempt)
                send_post_email(window_name, date_str, post.tweets)
                logger.info("post_main: post approved on attempt %d for window %s", attempt, window)
                return
            logger.warning("post_main: attempt %d rejected: %s", attempt, result.issues)

        save_post_text(ann_ids, None, 3)
        logger.warning("post_main: all 3 supervisor attempts failed for window %s", window)
        send_no_post_email(window_name, date_str, "Supervisor odrzucił wszystkie 3 próby.")

    except Exception as exc:
        logger.exception("post_main: pipeline failed")
        try:
            send_alert(exc)
            logger.info("post_main: alert email sent")
        except Exception as alert_exc:
            logger.error("post_main: failed to send alert: %s", alert_exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
