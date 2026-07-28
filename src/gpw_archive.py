"""Official GPW archive — one completed session per request (PUL-98).

Where `src/gpw_quotations.py` reads the *live* sheet, this reads the archive:
`https://www.gpw.pl/archiwum-notowan?type=<code>&instrument=&date=DD-MM-YYYY`.
For a session that has already closed, the archive is the only trustworthy oracle
— the live feed's `Kurs odn.` is adjusted for corporate actions and would
reintroduce the dividend defect this change exists to remove (GH #191).

Three properties of the page drive the design:

* The quotations sheet is **not** the first table. A CALL/PUT table without a
  thead precedes it, so selecting a table by position would parse options data as
  prices. The sheet is found by its headers.
* On a non-session date the sheet is absent entirely while that first table
  remains. Absence therefore means "no session" and returns `{}`.
* A **failed fetch must never be reported as `{}`.** Once retries are exhausted
  this raises `ScraperError`. Conflating the two would make a transient network
  fault look like a market holiday: a corrective pass would report a perfectly
  normal session as a phantom trading day, and a self-heal would skip a date it
  should have repaired — both silently.

Fetches are paced and retried because the archive resets connections both under
load and at rest; a 0.4 s cadence was measured to trigger `ConnectionReset`.
Use this module's `get` (httpx, via `src.http_client`) — `requests` is refused by
gpw.pl outright.
"""
import logging
import time
import unicodedata
from datetime import date

from bs4 import BeautifulSoup

from src.exceptions import ScraperError
from src.http_client import get
from src.polish_numbers import parse_polish_float

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://www.gpw.pl/archiwum-notowan"

# Instrument classes the archive exposes under `type`.
INSTRUMENT_SHARES = "10"
INSTRUMENT_ETF = "241"
INSTRUMENT_ETC = "560"
INSTRUMENT_ETN = "561"

# Field -> accepted header labels, normalized. `Nazwa` here is the exchange's
# abbreviated name (ALLEGRO, PKNORLEN), not the full company name.
_COLUMNS: dict[str, tuple[str, ...]] = {
    "name": ("nazwa",),
    "kurs_otwarcia": ("kurs otwarcia",),
    "kurs_max": ("kurs maksymalny",),
    "kurs_min": ("kurs minimalny",),
    "kurs_zamkniecia": ("kurs zamkniecia",),
    "zmiana_procentowa": ("zmiana kursu %",),
    "wartosc_obrotu": ("wartosc obrotu (w tys.)",),
}

# The archive publishes turnover in thousands; company_daily_stats holds PLN.
_TURNOVER_UNIT = 1000

_MIN_FETCH_INTERVAL_S = 1.5
_FETCH_RETRIES = 3
_last_fetch_at = 0.0


def _normalize(label: str) -> str:
    """Fold diacritics and collapse whitespace so 'Kurs zamknięcia' matches."""
    stripped = "".join(
        ch for ch in unicodedata.normalize("NFKD", label) if not unicodedata.combining(ch)
    )
    return " ".join(stripped.split()).lower()


def _resolve_columns(thead) -> dict[str, int]:
    """Map field -> column index, raising when a label the sheet must carry is gone.

    Failing loud here is deliberate. A renamed close column would otherwise yield
    a sheet of `None` closes, which reads downstream as "the session had no
    trades" — a wrong answer that looks like a right one.
    """
    header: dict[str, int] = {}
    for position, th in enumerate(thead.find_all("th")):
        header.setdefault(_normalize(th.get_text(" ", strip=True)), position)

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
        raise ScraperError(
            f"gpw archive header changed — missing {missing}; refusing to guess positions"
        )
    return resolved


def _find_sheet(soup):
    """Return the quotations table, or None when the date carried no session."""
    for table in soup.find_all("table"):
        thead = table.find("thead")
        if thead is None:
            continue
        labels = {_normalize(th.get_text(" ", strip=True)) for th in thead.find_all("th")}
        if "nazwa" in labels:
            return table
    return None


def _cell(cells: list, index: int) -> str:
    return cells[index].get_text(" ", strip=True) if index < len(cells) else ""


def _fetch(url: str):
    """Paced, retried GET. Raises ScraperError once the retries are spent."""
    global _last_fetch_at

    last_error: Exception | None = None
    for attempt in range(1, _FETCH_RETRIES + 1):
        elapsed = time.monotonic() - _last_fetch_at
        if elapsed < _MIN_FETCH_INTERVAL_S:
            time.sleep(_MIN_FETCH_INTERVAL_S - elapsed)
        try:
            resp = get(url)
            _last_fetch_at = time.monotonic()
            return resp
        except ScraperError as exc:
            last_error = exc
            _last_fetch_at = time.monotonic()
            logger.warning("fetch_archive_session: attempt %d/%d failed for %s",
                           attempt, _FETCH_RETRIES, url)
            if attempt < _FETCH_RETRIES:
                time.sleep(_MIN_FETCH_INTERVAL_S * attempt)
    raise ScraperError(f"gpw archive unreachable after {_FETCH_RETRIES} attempts: {url}") from (
        last_error
    )


def archive_url(session_date: date, instrument_type: str = INSTRUMENT_SHARES) -> str:
    """The archive's own URL shape: DD-MM-YYYY, and `instrument` deliberately empty."""
    return (
        f"{ARCHIVE_URL}?type={instrument_type}&instrument="
        f"&date={session_date.strftime('%d-%m-%Y')}"
    )


def fetch_archive_html(session_date: date, instrument_type: str = INSTRUMENT_SHARES) -> str:
    """Raw page for one date — split out so a bulk pass can cache what it fetched."""
    return _fetch(archive_url(session_date, instrument_type)).text


def fetch_archive_session(
    session_date: date, instrument_type: str = INSTRUMENT_SHARES
) -> dict[str, dict]:
    """Fetch one completed session from the GPW archive, keyed by the sheet's `Nazwa`.

    Returns `{}` when the date carried no session (weekend, holiday, or a session
    not yet published). Raises `ScraperError` when the fetch fails or the sheet's
    header no longer matches — neither is a holiday, and neither may look like one.
    """
    return parse_archive_sheet(fetch_archive_html(session_date, instrument_type), session_date)


def parse_archive_sheet(html: str, session_date: date | None = None) -> dict[str, dict]:
    """Parse one archive page. `session_date` only labels the logs."""
    soup = BeautifulSoup(html, "html.parser")
    table = _find_sheet(soup)
    if table is None:
        logger.info("fetch_archive_session: no session on %s", session_date)
        return {}

    columns = _resolve_columns(table.find("thead"))
    tbody = table.find("tbody")
    if tbody is None:
        logger.info("fetch_archive_session: sheet without a tbody on %s", session_date)
        return {}

    sheet: dict[str, dict] = {}
    for row in tbody.find_all("tr"):
        cells = row.find_all("td")
        name = _cell(cells, columns["name"])
        if not name:
            continue

        turnover_thousands = parse_polish_float(_cell(cells, columns["wartosc_obrotu"]))
        sheet[name] = {
            "kurs_otwarcia": parse_polish_float(_cell(cells, columns["kurs_otwarcia"])),
            "kurs_max": parse_polish_float(_cell(cells, columns["kurs_max"])),
            "kurs_min": parse_polish_float(_cell(cells, columns["kurs_min"])),
            "kurs_zamkniecia": parse_polish_float(_cell(cells, columns["kurs_zamkniecia"])),
            "zmiana_procentowa": parse_polish_float(_cell(cells, columns["zmiana_procentowa"])),
            "wartosc_obrotu": (
                turnover_thousands * _TURNOVER_UNIT if turnover_thousands is not None else None
            ),
        }

    logger.info("fetch_archive_session: %s parsed %d instruments", session_date, len(sheet))
    return sheet
