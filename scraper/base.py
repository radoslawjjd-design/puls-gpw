"""HTTP client — singleton Session z connection pooling i exponential backoff z full jitter."""
import logging
import random
import threading
import time

import requests
from requests.adapters import HTTPAdapter

from config import HEADERS, HTTP_RETRIES, HTTP_TIMEOUT, REQUEST_DELAY

logger = logging.getLogger(__name__)

_session: requests.Session | None = None
_session_lock = threading.Lock()

_POOL_CONNECTIONS = 10
_POOL_MAXSIZE     = 20


def _get_session() -> requests.Session:
    global _session
    if _session is not None:
        return _session
    with _session_lock:
        if _session is not None:
            return _session
        s = requests.Session()
        adapter = HTTPAdapter(pool_connections=_POOL_CONNECTIONS, pool_maxsize=_POOL_MAXSIZE)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        s.headers.update(HEADERS)
        _session = s
        return s


def _jittered_backoff(attempt: int) -> float:
    # AWS full-jitter pattern: base ± 100% — rozprasza retry po thundering herd (503 rate limit).
    base = REQUEST_DELAY * attempt
    return base + random.random() * base


def get(url: str, timeout: int = HTTP_TIMEOUT, retries: int = HTTP_RETRIES):
    session = _get_session()
    for attempt in range(1, retries + 1):
        try:
            time.sleep(REQUEST_DELAY)
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            logger.warning(f"[attempt {attempt}/{retries}] GET {url} — {e}")
            if attempt < retries:
                time.sleep(_jittered_backoff(attempt))
    logger.warning(f"Wszystkie próby nieudane: {url}")
    return None


def download_binary(url: str) -> bytes | None:
    resp = get(url, timeout=60)
    if resp is None:
        return None
    return resp.content
