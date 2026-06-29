"""GPW ETF/ETC/ETN listings scraper (PUL-67).

Fetches https://www.gpw.pl/etfy-pelna-wersja-notowan (static HTML, no JS needed).
Tables have class 'etf-footable'; price cells carry data-o-value attributes and
specific CSS classes (_rel, _open, _min, _max, _last, change, _c_vol).

Returns two structures:
- instruments: {ticker: {ticker, name, isin, instrument_type, created_at, updated_at}}
- quotes: list of {ticker, snapshot_date, kurs_zamkniecia, zmiana_procentowa,
                   zmiana_kwotowa, kurs_odn, kurs_otwarcia, kurs_min, kurs_max,
                   wolumen_skum, fetched_at}
"""
import logging
from datetime import date, datetime

from bs4 import BeautifulSoup

from src.exceptions import ScraperError
from src.http_client import get

logger = logging.getLogger(__name__)

GPW_ETF_URL = "https://www.gpw.pl/etfy-pelna-wersja-notowan"

_SECTION_TYPES = ("ETN", "ETC", "ETF")  # ordered so ETN checked before ETF


def _find_instrument_type(table) -> str | None:
    """Return ETF/ETC/ETN by scanning backwards for the nearest h3 heading."""
    heading = table.find_previous("h3")
    if heading is None:
        return None
    text = heading.get_text(strip=True).upper()
    for section in _SECTION_TYPES:
        if section in text:
            return section
    return None


def _data_o_value(cell) -> float | None:
    """Parse data-o-value attribute as float; return None when absent or non-numeric."""
    val = cell.get("data-o-value") if cell else None
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _find_cell(row, css_class: str):
    """Return the first <td> in row that has css_class in its class list."""
    return row.find("td", class_=css_class)


def fetch_etf_page(
    snapshot_date: date,
    fetched_at: datetime,
) -> tuple[dict[str, dict], list[dict]]:
    """Fetch and parse the GPW ETF/ETC/ETN listing page.

    Returns:
        instruments: {ticker: instrument master-data dict}
        quotes:      list of daily quote dicts
    """
    try:
        resp = get(GPW_ETF_URL)
    except ScraperError:
        logger.warning("fetch_etf_page: HTTP request failed for %s", GPW_ETF_URL)
        return {}, []
    soup = BeautifulSoup(resp.text, "html.parser")

    instruments: dict[str, dict] = {}
    quotes: list[dict] = []
    fetched_at_str = fetched_at.isoformat()
    snapshot_date_str = snapshot_date.isoformat()

    for table in soup.find_all("table", class_="etf-footable"):
        instrument_type = _find_instrument_type(table)
        if instrument_type is None:
            logger.warning("fetch_etf_page: could not determine type for table, skipping")
            continue

        tbody = table.find("tbody")
        if tbody is None:
            continue

        for row in tbody.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            # Ticker is in the second cell (col 1); strip b tag whitespace
            ticker_cell = cells[1]
            ticker = ticker_cell.get_text(strip=True)
            if not ticker:
                continue

            # ISIN is in the third cell (col 2)
            isin = cells[2].get_text(strip=True) or None

            kurs_odn = _data_o_value(_find_cell(row, "_rel"))
            kurs_otwarcia = _data_o_value(_find_cell(row, "_open"))
            kurs_min = _data_o_value(_find_cell(row, "_min"))
            kurs_max = _data_o_value(_find_cell(row, "_max"))
            kurs_zamkniecia = _data_o_value(_find_cell(row, "_last"))
            zmiana_procentowa = _data_o_value(_find_cell(row, "change"))
            wolumen_skum = _data_o_value(_find_cell(row, "_c_vol"))

            zmiana_kwotowa = None
            if kurs_odn is not None and zmiana_procentowa is not None:
                zmiana_kwotowa = kurs_odn * zmiana_procentowa / 100

            instruments[ticker] = {
                "ticker": ticker,
                "name": ticker,
                "isin": isin,
                "instrument_type": instrument_type,
                "created_at": fetched_at_str,
                "updated_at": fetched_at_str,
            }
            quotes.append({
                "ticker": ticker,
                "snapshot_date": snapshot_date_str,
                "kurs_zamkniecia": kurs_zamkniecia,
                "zmiana_procentowa": zmiana_procentowa,
                "zmiana_kwotowa": zmiana_kwotowa,
                "kurs_odn": kurs_odn,
                "kurs_otwarcia": kurs_otwarcia,
                "kurs_min": kurs_min,
                "kurs_max": kurs_max,
                "wolumen_skum": wolumen_skum,
                "fetched_at": fetched_at_str,
            })

    logger.info(
        "fetch_etf_page: parsed %d instruments, %d quotes",
        len(instruments), len(quotes),
    )
    return instruments, quotes
