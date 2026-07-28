"""Official GPW and NewConnect quotation tables (PUL-98).

Both exchanges publish the same AJAX-rendered table through `ajaxindex.php`, and
both carry the **official close** — the column bankier.pl does not publish, which
is why `company_daily_stats.kurs_zamkniecia` was wrong by 0.1–0.4%.

Column indices are derived from the header structure, never hard-coded. The two
layouts look interchangeable and are not: GPW has `Op. na pap.` and no `Segment`,
NewConnect the reverse, so `Skrót` sits at data index 4 on GPW and 3 on NewConnect
while everything from index 6 realigns. Reading GPW's positions against NewConnect
would silently return the currency column as the ticker. Positional indexing is
exactly how the defect this module replaces survived a month
(`src/bankier_metrics.py:98-105` reads `cells[1]..cells[8]`).

The parser mirrors src/gpw_etf_metrics.py: no `db.*` imports, and any failure
returns an empty dict for the caller to log rather than raising.
"""
import logging
import unicodedata

from bs4 import BeautifulSoup

from src.exceptions import ScraperError
from src.http_client import get
from src.polish_numbers import parse_int, parse_polish_float

logger = logging.getLogger(__name__)

GPW_QUOTATIONS_URL = (
    "https://www.gpw.pl/ajaxindex.php?action=GPWQuotations&start=showTable"
    "&tab=all&lang=PL&type=&full=1&format=html"
)
NC_QUOTATIONS_URL = (
    "https://newconnect.pl/ajaxindex.php?action=NCExternalDataFrontController"
    "&start=showTable&tab=all&lang=PL&full=1&format=html"
)

_MARKET_URLS = {"gpw": GPW_QUOTATIONS_URL, "nc": NC_QUOTATIONS_URL}

# Field -> accepted header labels, normalized. The close carries a different label
# on each market: GPW appends "/ zamk." because its session ends with a closing
# auction, NewConnect does not.
_COLUMNS: dict[str, tuple[str, ...]] = {
    "company_name": ("nazwa",),
    "isin": ("isin",),
    "ticker": ("skrot",),
    "kurs_odn": ("kurs odn.",),
    "kurs_otwarcia": ("kurs otw.",),
    "kurs_min": ("kurs min.",),
    "kurs_max": ("kurs maks.",),
    "kurs_zamkniecia": ("kurs ost. trans. / zamk.", "kurs ost. trans."),
    "zmiana_procentowa": ("zm.do k.odn. (%)",),
    "liczba_transakcji": ("liczba transakcji",),
    "wartosc_obrotu": ("wart. obr. - skumul. (tys.)",),
}

# The feed publishes cumulative turnover in thousands; company_daily_stats holds PLN.
_TURNOVER_UNIT = 1000


def _normalize(label: str) -> str:
    """Fold diacritics and collapse whitespace so 'Skrót' matches regardless of encoding."""
    stripped = "".join(
        ch for ch in unicodedata.normalize("NFKD", label) if not unicodedata.combining(ch)
    )
    return " ".join(stripped.split()).lower()


def _header_index(thead) -> dict[str, int]:
    """Map each first-row header label to the data-column index it starts at.

    Each `th` claims `colspan` data columns. GPW's two offer blocks carry
    colspan=3 and are detailed in a second header row we deliberately ignore —
    expanding the first row is what keeps the columns after them aligned.
    """
    rows = thead.find_all("tr")
    if not rows:
        return {}
    index: dict[str, int] = {}
    position = 0
    for th in rows[0].find_all("th"):
        index.setdefault(_normalize(th.get_text(" ", strip=True)), position)
        try:
            position += int(th.get("colspan") or 1)
        except (TypeError, ValueError):
            position += 1
    return index


def _resolve_columns(thead) -> dict[str, int] | None:
    """Return field -> column index, or None when any expected label is absent."""
    header = _header_index(thead)
    resolved: dict[str, int] = {}
    missing: list[str] = []
    for field, labels in _COLUMNS.items():
        for label in labels:
            if label in header:
                resolved[field] = header[label]
                break
        else:
            missing.append(labels[0])
    if missing:
        logger.warning(
            "fetch_quotations: unexpected header — missing %s; refusing to guess positions",
            missing,
        )
        return None
    return resolved


def _cell(cells: list, index: int) -> str:
    return cells[index].get_text(" ", strip=True) if index < len(cells) else ""


def fetch_quotations(market: str) -> dict[str, dict]:
    """Fetch one official quotation table, keyed by exchange abbreviation (`Skrót`).

    market: 'gpw' (main market) or 'nc' (NewConnect).

    Returns {} on any failure — unknown market, HTTP error, missing table, or a
    header that no longer matches. An empty result is a gap the caller logs; a
    wrong result would be a wrong price written against a real ticker.
    """
    url = _MARKET_URLS.get(market)
    if not url:
        logger.warning("fetch_quotations: unknown market=%s", market)
        return {}

    try:
        resp = get(url)
    except ScraperError:
        logger.warning("fetch_quotations: HTTP failed for market=%s", market)
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table")
    if table is None or table.find("thead") is None:
        logger.warning("fetch_quotations: no quotation table for market=%s", market)
        return {}

    columns = _resolve_columns(table.find("thead"))
    if columns is None:
        return {}

    tbody = table.find("tbody")
    if tbody is None:
        logger.warning("fetch_quotations: table has no tbody for market=%s", market)
        return {}

    result: dict[str, dict] = {}
    for row in tbody.find_all("tr"):
        cells = row.find_all("td")
        ticker = _cell(cells, columns["ticker"])
        if not ticker:
            continue

        kurs_zamkniecia = parse_polish_float(_cell(cells, columns["kurs_zamkniecia"]))
        kurs_odn = parse_polish_float(_cell(cells, columns["kurs_odn"]))
        turnover_thousands = parse_polish_float(_cell(cells, columns["wartosc_obrotu"]))

        result[ticker] = {
            "company_name": _cell(cells, columns["company_name"]) or None,
            "isin": _cell(cells, columns["isin"]) or None,
            "kurs_zamkniecia": kurs_zamkniecia,
            "kurs_odn": kurs_odn,
            "kurs_otwarcia": parse_polish_float(_cell(cells, columns["kurs_otwarcia"])),
            "kurs_min": parse_polish_float(_cell(cells, columns["kurs_min"])),
            "kurs_max": parse_polish_float(_cell(cells, columns["kurs_max"])),
            "zmiana_procentowa": parse_polish_float(
                _cell(cells, columns["zmiana_procentowa"])
            ),
            # Computed from the two prices rather than reconstructed from the
            # percentage, which the feed rounds to two decimals.
            "zmiana_kwotowa": (
                kurs_zamkniecia - kurs_odn
                if kurs_zamkniecia is not None and kurs_odn is not None
                else None
            ),
            "liczba_transakcji": parse_int(_cell(cells, columns["liczba_transakcji"])),
            "wartosc_obrotu": (
                turnover_thousands * _TURNOVER_UNIT if turnover_thousands is not None else None
            ),
        }

    logger.info("fetch_quotations: market=%s parsed %d instruments", market, len(result))
    return result
