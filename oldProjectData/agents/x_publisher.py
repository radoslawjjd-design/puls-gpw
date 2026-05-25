"""
Klient X (Twitter) API v2 — publikacja tweetów i wątków.

Używa tweepy z OAuth 1.0a (Consumer Key + Access Token).
Singleton pattern — jeden klient na sesję Cloud Run Job.

Compliance guard (F6.1 redesignu): KAŻDY tweet jest walidowany przez
agents.xpost_compliance.validate_compliance PRZED publikacją.
Reguły 2026: ≤1 cashtag, ≤2 hashtagi, ≤280 zn., znany ticker GPW.

Fail fast — gdy którykolwiek tweet w threadzie narusza compliance, publish()
raise ValueError ZANIM cokolwiek wyleci na X. Zero partial publishes
przez naruszenie compliance. To jest OSTATNIA zapora; F2 guards w validatorze
łapią wcześniej, ale tu jest belt-and-braces.

Użycie:
    from agents.x_publisher import get_x_publisher
    publisher = get_x_publisher()
    tweet_ids = publisher.publish(tweets=["tekst"], is_thread=False)
"""
import logging
import threading
import time

import tweepy

from agents.xpost_compliance import validate_compliance
from utils.gpw_tickers import get_gpw_tickers

logger = logging.getLogger(__name__)


def _validate_all_or_raise(tweets: list[str], window: str = "") -> None:
    """Compliance fail-fast: sprawdza WSZYSTKIE tweety przed pierwszym create_tweet.

    Raise ValueError z listą naruszeń (per tweet) gdy którykolwiek nie przejdzie.
    Brak partial publish — zero create_tweet jeśli compliance zatrzyma.

    FIX 2026-04-19 (CRITICAL pre-sunday-launch): window MUSI być przekazane do
    validate_compliance — inaczej sunday Premium long-form (1500-4000 zn/tweet)
    failuje na limit 280 zn (free-tier default) i sunday NIGDY nie idzie na X.
    """
    known = get_gpw_tickers()
    failures: list[str] = []
    for i, t in enumerate(tweets, start=1):
        result = validate_compliance(t, known_tickers=known, window=window)
        if not result.is_ok:
            for v in result.violations:
                failures.append(f"Tweet {i}/{len(tweets)}: {v}")
    if failures:
        raise ValueError(
            "Post X odrzucony przez compliance guard (F6.1):\n  - "
            + "\n  - ".join(failures)
        )


class XPublisher:
    """Klient X API v2 do publikacji tweetów."""

    def __init__(self, api_key: str, api_secret: str,
                 access_token: str, access_secret: str):
        self._client = tweepy.Client(
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_secret,
        )
        logger.info("X API klient zainicjalizowany")

    def post_single(self, text: str) -> str:
        """Publikuje pojedynczy tweet. Zwraca tweet_id."""
        response = self._client.create_tweet(text=text)
        tid = str(response.data["id"])
        logger.info(f"Tweet opublikowany: ID={tid}, {len(text)} znaków")
        return tid

    def post_thread(self, tweets: list[str]) -> list[str]:
        """Publikuje wątek (reply chain). Zwraca listę tweet_ids.
        Przy błędzie na tweecie N — rzuca exception, ale _last_partial_ids
        zawiera IDs tweetów opublikowanych przed błędem."""
        self._last_partial_ids = []
        tweet_ids = []
        reply_to = None
        for i, tweet_text in enumerate(tweets):
            response = self._client.create_tweet(
                text=tweet_text,
                in_reply_to_tweet_id=reply_to,
            )
            tid = str(response.data["id"])
            tweet_ids.append(tid)
            self._last_partial_ids = list(tweet_ids)
            reply_to = tid
            logger.info(f"Tweet {i+1}/{len(tweets)} opublikowany: ID={tid}")
            if i < len(tweets) - 1:
                time.sleep(1)  # rate limit safety
        return tweet_ids

    def publish(self, tweets: list[str], is_thread: bool, window: str = "") -> list[str]:
        """Główna metoda — pojedynczy tweet lub wątek. Zwraca tweet_ids.

        Compliance guard (F6.1): WALIDUJE wszystkie tweety PRZED pierwszym
        create_tweet. ValueError → re-raise (caller decyduje co dalej, np.
        log + Sentry). Tylko błędy z tweepy.create_tweet są ciche → partial_ids.

        Args:
            window: nazwa okna xpost (np. "sunday"). KRYTYCZNE dla sunday Premium
                long-form (5000 zn limit) — bez window default 280 zn limit blokuje
                wszystkie sunday tweety (1500-4000 zn).
        """
        # Compliance fail-fast — raise PRZED jakimkolwiek create_tweet
        _validate_all_or_raise(tweets, window=window)

        try:
            if is_thread and len(tweets) > 1:
                return self.post_thread(tweets)
            return [self.post_single(tweets[0])]
        except Exception as e:
            # PR#14 #6 fix (2026-04-20): partial publish + Sentry alert.
            # Wcześniej swallow → caller widział tweet_ids=[partial] jak success
            # i zapisywał do BQ "posted_to_x=True" mimo że thread niekompletny.
            # Teraz: log + Sentry + zwróć partial (caller MUSI sprawdzić len()
            # vs oczekiwane lub trapnąć attribute `last_publish_error`).
            self._last_publish_error = e
            partial = self._partial_ids
            logger.error(
                f"Błąd publikacji na X: {e}. Opublikowano {len(partial)} z "
                f"{len(tweets)} tweetów. Caller MUSI sprawdzić last_publish_error."
            )
            try:
                import sentry_sdk
                sentry_sdk.capture_exception(e)
                sentry_sdk.capture_message(
                    f"X publish PARTIAL: {len(partial)}/{len(tweets)} tweetów "
                    f"opublikowanych przed błędem ({type(e).__name__}: {e}). "
                    f"Window={window}, posted_ids={partial}.",
                    level="error",
                )
            except ImportError:
                pass
            return partial

    @property
    def _partial_ids(self) -> list[str]:
        """Zwraca IDs tweetów opublikowanych przed błędem (z ostatniego post_thread)."""
        return getattr(self, "_last_partial_ids", [])


# ── Singleton ────────────────────────────────────────────────────────────────

_publisher: XPublisher | None = None
_lock = threading.Lock()


def get_x_publisher() -> XPublisher:
    """Singleton — inicjalizuje XPublisher z env vars (ładowane przez bootstrap)."""
    global _publisher
    with _lock:
        if _publisher is None:
            import os
            api_key       = os.environ.get("X_API_KEY", "")
            api_secret    = os.environ.get("X_API_SECRET", "")
            access_token  = os.environ.get("X_ACCESS_TOKEN", "")
            access_secret = os.environ.get("X_ACCESS_SECRET", "")
            if not all([api_key, api_secret, access_token, access_secret]):
                raise ValueError(
                    "Brak kluczy X API — ustaw env vars: "
                    "X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET"
                )
            _publisher = XPublisher(api_key, api_secret, access_token, access_secret)
    return _publisher
