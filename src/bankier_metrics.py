import logging
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from src.exceptions import ScraperError
from src.http_client import get

logger = logging.getLogger(__name__)

_LISTING_URLS = {
    "akcje": "https://www.bankier.pl/gielda/notowania/akcje",
    "new-connect": "https://www.bankier.pl/gielda/notowania/new-connect",
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
    """Parse a Polish-formatted number (comma decimal, space thousands separator) to float."""
    try:
        cleaned = (
            text.replace("\xa0", "")
                .replace("zł", "")
                .replace("%", "")
                .replace(" ", "")
                .strip()
        )
        return float(cleaned.replace(",", ".")) if cleaned else None
    except (ValueError, AttributeError):
        return None


def _parse_int(text: str) -> int | None:
    """Parse a Polish-formatted integer (space thousands separator) to int."""
    try:
        cleaned = text.replace("\xa0", "").replace(" ", "").strip()
        return int(cleaned) if cleaned else None
    except (ValueError, AttributeError):
        return None


def fetch_listing_page(market: str) -> dict[str, dict]:
    """Fetch the bankier notowania listing page for a market.

    market: 'akcje' (GPW main) or 'new-connect' (NewConnect)

    Returns a dict keyed by bankier symbol with trading-data fields, or an
    empty dict on HTTP failure or missing table — callers log the gap and skip
    those tickers.
    """
    url = _LISTING_URLS.get(market)
    if not url:
        logger.warning("fetch_listing_page: unknown market=%s", market)
        return {}

    try:
        resp = get(url)
    except ScraperError:
        logger.warning("fetch_listing_page: HTTP failed for market=%s", market)
        return {}

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", class_="m-quotes-data-table")
        if not table:
            logger.warning("fetch_listing_page: table not found for market=%s", market)
            return {}

        result: dict[str, dict] = {}
        for row in table.find_all("tr")[1:]:  # skip header row
            cells = row.find_all("td")
            if len(cells) < 9:
                continue
            a = cells[0].find("a")
            if not a:
                continue
            href = a.get("href", "")
            symbol = parse_qs(urlparse(href).query).get("symbol", [None])[0]
            if not symbol:
                continue
            result[symbol] = {
                "company_name": a.get_text(strip=True) or None,
                "kurs_zamkniecia": _parse_polish_float(cells[1].get_text()),
                "zmiana_procentowa": _parse_polish_float(cells[2].get_text()),
                "zmiana_kwotowa": _parse_polish_float(cells[3].get_text()),
                "liczba_transakcji": _parse_int(cells[4].get_text()),
                "wartosc_obrotu": _parse_polish_float(cells[5].get_text()),
                "kurs_otwarcia": _parse_polish_float(cells[6].get_text()),
                "kurs_max": _parse_polish_float(cells[7].get_text()),
                "kurs_min": _parse_polish_float(cells[8].get_text()),
            }

        logger.info("fetch_listing_page: market=%s loaded %d symbols", market, len(result))
        return result

    except Exception:
        logger.warning("fetch_listing_page: parse failed for market=%s", market, exc_info=True)
        return {}
