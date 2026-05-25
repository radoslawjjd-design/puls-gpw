import logging
import random
import threading
import time

import requests
from requests.adapters import HTTPAdapter

from config import HEADERS, HTTP_RETRIES, HTTP_TIMEOUT, REQUEST_DELAY

logger = logging.getLogger(__name__)

# ── Session singleton z connection pooling (audit #6, 2026-04-14) ──────────
# Reuse TCP/SSL connection per host eliminuje ~100-200ms handshake per call.
# Shared session jest thread-safe (requests.Session() jest thread-safe od 2.x).
_session: requests.Session | None = None
_session_lock = threading.Lock()

# Pool tuning — scraper bije ~3 hosty (bankier.pl, gpwbenchmark.pl, stooq.pl).
# 20 connections per pool wystarcza dla ProfileWorkers=5 + GeminiWorkers=5
# bez czekania na slot.
_POOL_CONNECTIONS = 10  # liczba unikalnych poolów (~hosts)
_POOL_MAXSIZE     = 20  # max connections per pool


def _get_session() -> requests.Session:
    """Singleton requests.Session z pre-mounted HTTPAdapter z poolami.

    Thread-safe (lock na inicjalizację). Stworzona raz, reused dla
    wszystkich `get()` calls w procesie.
    """
    global _session
    if _session is not None:
        return _session
    with _session_lock:
        if _session is not None:
            return _session
        s = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=_POOL_CONNECTIONS,
            pool_maxsize=_POOL_MAXSIZE,
        )
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        s.headers.update(HEADERS)
        _session = s
        return s


def _jittered_backoff(attempt: int) -> float:
    """Exponential-ish backoff z full jitter (AWS pattern).

    base = REQUEST_DELAY * attempt (deterministyczna podstawa).
    jitter = uniform [0, base] (do 100% dodatkowo).
    Wynik ∈ [base, 2*base), średnio 1.5*base.

    Powód: gdy N workerów dostanie 503 w tym samym momencie (np. rate limit
    Bankier), bez jittera WSZYSCY retry'ują dokładnie po `REQUEST_DELAY * 1`
    sekund → kolejny thundering herd i kolejne 503. Jitter rozprasza retry
    po czasie, zwiększając szansę że serwer zdąży się zregenerować.
    """
    base = REQUEST_DELAY * attempt
    return base + random.random() * base


def get(url: str, timeout: int = HTTP_TIMEOUT, retries: int = HTTP_RETRIES):
    session = _get_session()
    for attempt in range(1, retries + 1):
        try:
            time.sleep(REQUEST_DELAY)
            # Headers ustawione na session (s.headers.update) — bez per-call.
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            logger.warning(f"[attempt {attempt}/{retries}] GET {url} — {e}")
            if attempt < retries:
                time.sleep(_jittered_backoff(attempt))
    # Transient: Bankier 503/timeout — pipeline handles gracefully, no Sentry mail.
    logger.warning(f"Wszystkie próby nieudane: {url}")
    return None


def download_binary(url: str):
    resp = get(url, timeout=60)
    if resp is None:
        return None
    return resp.content