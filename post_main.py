"""Cloud Run Job entrypoint for the X post generation pipeline."""
import argparse
import logging
import os
import re
import sys
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from src.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

from db.bigquery import (
    create_table_if_not_exists,
    create_x_posts_table_if_not_exists,
    ensure_schema_current,
    ensure_x_posts_schema_current,
    fetch_top_n_for_window,
    save_x_post,
    update_x_post_publish_result,
    x_post_already_published,
)
from src.exceptions import XPublishPartialError
from src.notifier import send_alert, send_no_post_email, send_post_email
from src.post_generator import generate_post
from src.post_selection import NUMBER_DEPENDENT_EVENT_TYPES
from src.post_supervisor import validate_post
from src.x_publisher import get_x_publisher

WARSAW = ZoneInfo("Europe/Warsaw")
_MAX_ATTEMPTS = 3

# Pipeline quality gate (PUL-27): announcements below this analysis_score never enter
# a post. Filtered at fetch time, so it gates generation + email + publish together.
# 50 = floor of a genuine named, value-relevant event for an untiered company; below
# sits the noise floor. Tunable — observe ~1 week, raise to 60–65 if needed.
MIN_XPOST_SCORE = 50

# Auto-publish flag (default OFF). Only "true" (case-insensitive) enables publishing.
X_AUTO_PUBLISH = os.environ.get("X_AUTO_PUBLISH", "").lower() == "true"

# A real company-analysis thread carries exactly one $CASHTAG (the top-score company,
# in the hook). Its presence anywhere in the thread is the substance signal.
_CASHTAG_RE = re.compile(r"\$[A-Z0-9]{1,10}")
# Substance-less / placeholder markers that must never reach X (hard constraint).
# Keep these specific: substring-matched against the joined thread, so a loose
# marker like "brak post" would wrongly trip on legit Polish "brak postępów".
_PLACEHOLDER_MARKERS = ("brak posta", "lorem ipsum", "placeholder", "[todo")


def is_publishable(tweets: list[str]) -> bool:
    """Belt-and-braces substance guard, independent of post_supervisor.

    A thread is publishable only if it is a genuine, non-empty company-analysis
    thread: at least 3 tweets (hook + ≥1 company body + closing), non-blank joined
    text, at least one $TICKER cashtag somewhere in the thread (the top-score company,
    carried in the hook), and no placeholder/"brak posta" marker. The supervisor
    occasionally approves an empty/degenerate thread; this guard is the independent
    stop before publish().
    """
    if not tweets or len(tweets) < 3:
        return False
    if not "".join(tweets).strip():
        return False
    body = tweets[1:-1]
    if not any(t and t.strip() for t in body):
        return False
    if not any(_CASHTAG_RE.search(t) for t in tweets):
        return False
    joined_lower = "\n".join(tweets).lower()
    if any(marker in joined_lower for marker in _PLACEHOLDER_MARKERS):
        return False
    return True


def _results_tweets_have_numbers(
    tweets: list[str], announcements: list[dict] | None
) -> bool:
    """Belt (Defect B defense-in-depth): every results body tweet must carry a digit.

    For each announcement whose ``event_type`` is number-dependent
    (``NUMBER_DEPENDENT_EVENT_TYPES``), locate its body tweet by the parenthesised
    ticker (``( $TICKER )`` / ``( TICKER )`` — the same match
    ``post_supervisor.validate_post`` uses) and confirm it contains at least one
    digit. Returns False (block publish) if any such results tweet is digit-less.

    "Has a number" = ``\\d`` present — intentionally coarse (a stray "Q1"/"2025"
    would pass). This is acceptable: Phase 1's selection drop already guarantees
    non-empty ``key_numbers`` for any surviving ``wyniki_*`` row, so this belt is
    genuine defense-in-depth, not the primary guarantee. Body tweets are
    ``tweets[1:-1]``; a results announcement with no matching body tweet is left
    alone (the supervisor already enforces ticker presence on approval).
    """
    body = tweets[1:-1]
    for ann in announcements or []:
        if ann.get("event_type") not in NUMBER_DEPENDENT_EVENT_TYPES:
            continue
        ticker = ann.get("ticker")
        if not ticker:
            continue
        pattern = rf"\(\s*\$?{re.escape(ticker)}\s*\)"
        tweet = next((t for t in body if re.search(pattern, t)), None)
        if tweet is None:
            continue
        if not re.search(r"\d", tweet):
            logger.warning(
                "post_main: results tweet for %s carries no number — not publishing", ticker
            )
            return False
    return True


def _persist_and_alert(
    x_post_id: str,
    tweet_ids: list[str] | None,
    status: str,
    exc: Exception,
) -> None:
    """Persist a failure/partial publish status and alert; never raises."""
    try:
        update_x_post_publish_result(x_post_id, tweet_ids, status)
    except Exception:
        logger.exception("post_main: failed to persist publish status %s", status)
    try:
        send_alert(exc)
    except Exception:
        logger.error("post_main: failed to send publish alert")


def _publish_to_x(
    tweets: list[str],
    window: str,
    x_post_id: str,
    announcements: list[dict] | None = None,
) -> tuple[str, list[str] | None]:
    """Publish the approved thread to X when allowed; persist outcome + alert on failure.

    Returns (status, published_ids). Status ∈ published|skipped|failed|partial.
    Never raises — the owner email and job completion must happen regardless of the
    publish outcome. Skips (no publish) when: the flag is OFF, the thread fails the
    substance guard, a number-dependent results tweet carries no amount, or the window
    was already published today (idempotency).
    """
    try:
        if not X_AUTO_PUBLISH:
            update_x_post_publish_result(x_post_id, None, "skipped")
            return "skipped", None
        if not is_publishable(tweets):
            logger.warning("post_main: thread failed substance guard — not publishing")
            update_x_post_publish_result(x_post_id, None, "skipped")
            return "skipped", None
        if not _results_tweets_have_numbers(tweets, announcements):
            update_x_post_publish_result(x_post_id, None, "skipped")
            return "skipped", None
        if x_post_already_published(window):
            logger.info("post_main: window %s already published today — not re-posting", window)
            update_x_post_publish_result(x_post_id, None, "skipped")
            return "skipped", None
        published_ids = get_x_publisher().publish_thread(tweets)
        update_x_post_publish_result(x_post_id, published_ids, "published")
        logger.info("post_main: published %d tweets to X for window %s", len(published_ids), window)
        return "published", published_ids
    except XPublishPartialError as exc:
        logger.error("post_main: partial X publish for window %s: %s", window, exc)
        _persist_and_alert(x_post_id, exc.published_ids, "partial", exc)
        return "partial", exc.published_ids
    except Exception as exc:
        logger.exception("post_main: X publish failed for window %s", window)
        _persist_and_alert(x_post_id, None, "failed", exc)
        return "failed", None

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
        # Self-sufficient schema setup: the post job must guarantee both the x_posts
        # table and the announcements.x_post_id column exist before the first write,
        # independent of whether the scraper has run since deploy. All idempotent.
        create_table_if_not_exists()
        ensure_schema_current()
        create_x_posts_table_if_not_exists()
        ensure_x_posts_schema_current()  # migrate x_publish_status onto existing x_posts

        window_start, window_end = _window_bounds(window, now_warsaw)
        announcements = fetch_top_n_for_window(
            window_start, window_end, n=4, min_score=MIN_XPOST_SCORE
        )

        ann_ids = [a["announcement_id"] for a in announcements]
        # Dedup tickers here for supervisor; generate_post deduplicates independently internally
        tickers = list(dict.fromkeys(a["ticker"] for a in announcements if a.get("ticker")))
        # Build scores in the same dedup order as generate_post (first occurrence per ticker)
        _seen_t: set[str] = set()
        company_scores: list[float | None] = []
        for _a in announcements:
            _t = _a.get("ticker") or ""
            if _t and _t not in _seen_t:
                _seen_t.add(_t)
                company_scores.append(_a.get("analysis_score"))

        # Guard on tickers (not len(announcements)) — rows without a ticker can't form a post
        if not tickers:
            logger.info("post_main: no valid-ticker announcements for %s — skipping", window)
            if window != "poludnie":
                send_no_post_email(window_name, date_str, "Brak zatwierdzonych ogłoszeń w oknie.")
            return
        expected_tweets = len(tickers) + 2

        post = None
        previous_issues: list[str] | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            post = generate_post(announcements, window=window, previous_issues=previous_issues)
            if post is None:
                logger.warning("post_main: generate_post returned None on attempt %d", attempt)
                continue
            result = validate_post(post, tickers, expected_tweets=expected_tweets)
            if result.approved:
                # Order matters: save_x_post first (returns x_post_id) so the publish
                # write + idempotency guard have a row to key on. Publish sits inside
                # the approved branch so unapproved threads can never publish.
                x_post_id = save_x_post(ann_ids, "\n\n".join(post.tweets), window, attempt)
                publish_status, published_ids = _publish_to_x(
                    post.tweets, window, x_post_id, announcements
                )
                send_post_email(
                    window_name, date_str, post.tweets, company_scores,
                    publish_status=publish_status, tweet_ids=published_ids,
                )
                logger.info(
                    "post_main: post approved on attempt %d for window %s (publish=%s)",
                    attempt, window, publish_status,
                )
                return
            logger.warning("post_main: attempt %d rejected: %s", attempt, result.issues)
            previous_issues = result.issues

        save_x_post(ann_ids, None, window, _MAX_ATTEMPTS)
        logger.warning("post_main: all %d supervisor attempts failed for window %s", _MAX_ATTEMPTS, window)
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
