import logging
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

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


def _parse_polish_float(text: str) -> float | None:
    """Parse a Polish-formatted number (comma decimal, 'zł' unit or '%') to float."""
    try:
        cleaned = (
            text.replace("\xa0", "").replace("zł", "").replace("%", "").strip()
        )
        return float(cleaned.replace(",", ".")) if cleaned else None
    except (ValueError, AttributeError):
        return None


def fetch_profile_price(hop_url: str) -> dict | None:
    """Scrape kurs_zamkniecia, zmiana_procentowa, zmiana_kwotowa from the profile page.

    Returns a dict with those keys (values may be None if individual fields fail to
    parse), or None on HTTP failure or missing price box — callers treat None as
    "insert NULLs for these fields" rather than a skip.
    """
    try:
        resp = get(hop_url)
    except ScraperError:
        logger.warning("fetch_profile_price: HTTP failed for %s", hop_url)
        return None

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        box = soup.find("div", class_="o-quotes-profile-header-box__numbers")
        if box is None:
            logger.warning("fetch_profile_price: price box not found for %s", hop_url)
            return None

        price_div = box.find("div", class_="o-quotes-profile-header-box__price")
        change_div = box.find("div", class_="o-quotes-profile-header-box__change")

        kurs_zamkniecia = None
        zmiana_procentowa = None
        zmiana_kwotowa = None

        if price_div:
            el = price_div.find("span", class_="-value")
            if el:
                kurs_zamkniecia = _parse_polish_float(el.get_text())

        if change_div:
            el = change_div.find("span", class_="-percentage-change")
            if el:
                zmiana_procentowa = _parse_polish_float(el.get_text())
            el = change_div.find("span", class_="-value-change")
            if el:
                zmiana_kwotowa = _parse_polish_float(el.get_text())

        return {
            "kurs_zamkniecia": kurs_zamkniecia,
            "zmiana_procentowa": zmiana_procentowa,
            "zmiana_kwotowa": zmiana_kwotowa,
        }
    except Exception:
        logger.warning("fetch_profile_price: parse failed for %s", hop_url, exc_info=True)
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
