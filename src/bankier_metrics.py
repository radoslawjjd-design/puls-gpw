import logging
from urllib.parse import parse_qs, urlparse

from src.exceptions import ScraperError
from src.http_client import get

logger = logging.getLogger(__name__)

_BANKIER_API_URL = "https://api.bankier.pl/quotes/public/company-profile-chart/{isin}/?symbols={symbol}&metrics=true&today=true"

_FIELD_MAP = {
    "kurs_odniesienia": "Kurs_odniesienia",
    "kurs_otwarcia": "Kurs_otwarcia",
    "kurs_min": "Minimum",
    "kurs_max": "Maximum",
    "wolumen_obrotu": "Wolumen_obrotu_szt",
    "wartosc_obrotu": "Wartosc_obrotu_zl",
    "liczba_transakcji": "Liczba_transakcji",
    "stopa_zwrotu_1r": "Stopa_zwrotu_1R",
    "kapitalizacja": "Kapitalizacja",
    "rynek": "Rynek",
    "system": "System_notowan",
}


def symbol_from_hop_url(hop_url: str) -> str | None:
    """Extract the bankier `symbol` query param from a stored hop_url.

    Returns None when the param is absent or the URL is malformed.
    """
    try:
        parsed = urlparse(hop_url)
        params = parse_qs(parsed.query)
        values = params.get("symbol")
        if not values:
            return None
        return values[0] or None
    except Exception:
        return None


def fetch_daily_stats(isin: str, symbol: str) -> dict | None:
    """Fetch today's trading-data snapshot from bankier's public JSON API.

    Returns a dict with normalised snake_case keys, or None on HTTP failure
    (ScraperError from http_client) — callers skip+log on None.
    """
    url = _BANKIER_API_URL.format(isin=isin, symbol=symbol)
    try:
        resp = get(url)
    except ScraperError:
        logger.warning("fetch_daily_stats: HTTP failed for isin=%s symbol=%s", isin, symbol)
        return None

    try:
        data = resp.json()
    except (ValueError, AttributeError):
        logger.warning("fetch_daily_stats: invalid JSON for isin=%s symbol=%s", isin, symbol)
        return None

    metrics = data.get("profile_data") if isinstance(data, dict) else None
    if metrics is None:
        logger.warning(
            "fetch_daily_stats: profile_data missing in response for isin=%s symbol=%s", isin, symbol
        )
        return None

    return {key: metrics.get(api_key) for key, api_key in _FIELD_MAP.items()}
