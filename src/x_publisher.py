"""X (Twitter) publisher — OAuth 1.0a user-context client via tweepy.

Thin transport layer: builds a tweepy.Client from the four X_* env vars and
publishes a thread as a reply-chain. This module performs NO flag reading, NO
compliance/non-empty checks, and NO BigQuery/email side effects — those live in
the caller (post_main.py). It only knows how to post tweets and report what it
posted.

Status taxonomy (decided by the caller from what this module raises):
  - returns ids        → published
  - XPublishPartialError → partial (a half-thread is live on X)
  - XPublisherError      → failed (nothing was posted)

Usage:
    from src.x_publisher import get_x_publisher
    ids = get_x_publisher().publish_thread(["hook", "body", "closing"])
"""
import logging
import os
import threading

import tweepy

from src.exceptions import XPublisherError, XPublishPartialError

logger = logging.getLogger(__name__)

_CRED_VARS = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")


def _clean(value: str) -> str:
    # Secret Manager can inject a BOM (﻿) and CRLF when secrets are created from
    # files with Windows line endings or UTF-8-BOM encoding (see src/notifier.py).
    return value.strip().lstrip("﻿")


class XPublisher:
    """Publishes single tweets or reply-chained threads to X."""

    def __init__(self, api_key: str, api_secret: str,
                 access_token: str, access_secret: str):
        self._client = tweepy.Client(
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_secret,
        )
        logger.info("X API client initialized")

    def publish_thread(self, tweets: list[str]) -> list[str]:
        """Publish `tweets` in order as a reply-chain; return all published ids.

        Each tweet after the first replies to the previous one's id. A
        single-element list posts a single tweet. On a mid-thread failure:
          - if ≥1 tweet was already posted → raise XPublishPartialError carrying
            the ids posted so far (a half-thread is live on X);
          - if 0 tweets were posted (e.g. the first create_tweet fails) → raise
            XPublisherError (nothing is live, so this is a full failure).
        """
        published_ids: list[str] = []
        reply_to: str | None = None
        for i, text in enumerate(tweets):
            try:
                response = self._client.create_tweet(
                    text=text,
                    in_reply_to_tweet_id=reply_to,
                )
                tid = str(response.data["id"])
            except Exception as exc:
                if published_ids:
                    logger.error(
                        "X thread failed on tweet %d/%d after posting %d: %s",
                        i + 1, len(tweets), len(published_ids), exc,
                    )
                    raise XPublishPartialError(published_ids, exc) from exc
                logger.error("X publish failed on first tweet: %s", exc)
                raise XPublisherError(f"X publish failed, nothing posted: {exc}") from exc
            published_ids.append(tid)
            reply_to = tid
            logger.info("Tweet %d/%d published: id=%s", i + 1, len(tweets), tid)
        return published_ids


# ── Singleton ─────────────────────────────────────────────────────────────────

_publisher: XPublisher | None = None
_publisher_lock = threading.Lock()


def get_x_publisher() -> XPublisher:
    """Singleton accessor; builds the client from env vars on first call.

    Raises XPublisherError (fail-fast) if any of the four X_* credentials is
    missing or blank. Reads creds here — NOT at import time — so importing this
    module never requires credentials.
    """
    global _publisher
    if _publisher is None:
        with _publisher_lock:
            if _publisher is None:
                creds = {var: _clean(os.environ.get(var, "")) for var in _CRED_VARS}
                missing = [var for var, val in creds.items() if not val]
                if missing:
                    raise XPublisherError(
                        "Missing X API credentials — set env vars: "
                        + ", ".join(missing)
                    )
                _publisher = XPublisher(
                    creds["X_API_KEY"],
                    creds["X_API_SECRET"],
                    creds["X_ACCESS_TOKEN"],
                    creds["X_ACCESS_SECRET"],
                )
    return _publisher
