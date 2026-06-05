import logging
import os
import threading
import time

import httpx

from src.exceptions import ScraperError

logger = logging.getLogger(__name__)

_REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "0.5"))
_MAX_RETRIES = int(os.environ.get("HTTP_MAX_RETRIES", "3"))
_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "30"))
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; puls-gpw/1.0)"}

_http_client: httpx.Client | None = None
_http_client_lock = threading.Lock()


def _get_http_client() -> httpx.Client:
    global _http_client
    if _http_client is None:
        with _http_client_lock:
            if _http_client is None:
                _http_client = httpx.Client(
                    headers=_HEADERS,
                    timeout=_TIMEOUT,
                    follow_redirects=True,
                )
    return _http_client


def get(url: str) -> httpx.Response:
    """GET z rate limit (0.5s) i retry (3×, exp backoff).

    Raises ScraperError after _MAX_RETRIES failed attempts.
    """
    client = _get_http_client()
    for attempt in range(1, _MAX_RETRIES + 1):
        if attempt > 1:
            time.sleep(_REQUEST_DELAY)
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "GET %s attempt %d/%d — HTTP %d",
                url, attempt, _MAX_RETRIES, exc.response.status_code,
            )
        except httpx.RequestError as exc:
            logger.warning("GET %s attempt %d/%d — %s", url, attempt, _MAX_RETRIES, exc)
        if attempt < _MAX_RETRIES:
            time.sleep(_REQUEST_DELAY * attempt)
    raise ScraperError(f"All {_MAX_RETRIES} attempts failed for {url}")
