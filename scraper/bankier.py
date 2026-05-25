"""
Scraper Bankier.pl ESPI/EBI.

Główna funkcja: collect_bankier(since: datetime) → list[dict]

Różnica względem starego projektu: zamiast filtrować po docelowej dacie,
filtrujemy po oknie czasowym — pobieramy ogłoszenia z ostatnich N minut
(zdefiniowane przez `since`, Warsaw tz). Pasuje do wywołania co 15 minut.
"""
import logging
import re
import zoneinfo
from datetime import date, datetime

from bs4 import BeautifulSoup

from config import BANKIER_BASE_URL, MAX_PAGES_BANKIER
from scraper.base import get
from utils.company import get_folder_name

logger = logging.getLogger(__name__)

WARSAW_TZ = zoneinfo.ZoneInfo("Europe/Warsaw")


def _parse_dt(text: str) -> datetime | None:
    text = text.strip()
    for fmt in ("%d-%m-%Y %H:%M", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_date(text: str) -> date | None:
    text = text.strip()
    for fmt in ("%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    m = re.search(r'(\d{2})[-.](\d{2})[-.](\d{4})', text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


def collect_bankier(since: datetime) -> list[dict]:
    """
    Zbiera ogłoszenia ESPI/EBI opublikowane od `since` (Warsaw tz).

    Zwraca listę dicts z polami:
        title, url, bankier_url, company, date, pub_time, source
    """
    if since.tzinfo is None:
        since = since.replace(tzinfo=WARSAW_TZ)

    results     = []
    found_older = False

    diag: dict[str, int] = {"items_found": 0, "no_date": 0, "older": 0, "future": 0, "unparseable": 0}
    sample_date: str | None = None

    for page in range(1, MAX_PAGES_BANKIER + 1):
        url = BANKIER_BASE_URL.format(page=page)
        logger.info(f"Bankier strona {page}: {url}")

        resp = get(url)
        if resp is None:
            logger.warning(f"Nie można pobrać strony {page}")
            break

        soup  = BeautifulSoup(resp.text, "html5lib")
        items = (
            soup.select(".m-quotes-announcements-item") or
            soup.select(".listingItem") or
            soup.select("li[class*='item']")
        )

        if not items:
            logger.warning(f"Bankier strona {page}: brak elementów (zmiana HTML?)")
            break

        diag["items_found"] += len(items)
        page_had_target = False

        for item in items:
            date_el = item.select_one(
                ".m-quotes-announcements-item__date, "
                ".date, time, [class*='date']"
            )
            if not date_el:
                diag["no_date"] += 1
                sample_date = sample_date or "<no date_el>"
                continue

            raw = (date_el.get("datetime") or date_el.get("content") or date_el.get_text(strip=True))
            raw_str = raw[:20]

            parsed_dt = _parse_dt(raw_str)
            if parsed_dt:
                # Traktujemy Bankier jako Warsaw time (strona polska, brak TZ w znaczniku)
                item_aware = parsed_dt.replace(tzinfo=WARSAW_TZ)
            else:
                item_date = _parse_date(raw_str)
                if item_date is None:
                    diag["unparseable"] += 1
                    sample_date = sample_date or f"<unparseable:{raw_str!r}>"
                    continue
                # Brak godziny — konserwatywnie: jeśli data dzisiejsza lub nowsza, włącz
                if item_date < since.date():
                    found_older = True
                    diag["older"] += 1
                    sample_date = sample_date or str(item_date)
                    continue
                item_aware = None  # nie mamy czasu, ale data >= since.date()

            if item_aware is not None:
                if item_aware > datetime.now(WARSAW_TZ):
                    diag["future"] += 1
                    sample_date = sample_date or str(item_aware.date())
                    continue
                if item_aware < since:
                    # Nie zakładamy chronologicznego porządku (może być interleaved).
                    # found_older sygnalizuje że przekroczyliśmy granicę — OUTER decyduje
                    # czy warto iść na kolejną stronę na podstawie page_had_target.
                    found_older = True
                    diag["older"] += 1
                    sample_date = sample_date or str(item_aware)
                    continue

            page_had_target = True

            link_el = item.select_one("a[href]")
            if not link_el:
                continue

            title    = link_el.get_text(strip=True)
            href_raw = link_el["href"]
            href     = href_raw if isinstance(href_raw, str) else href_raw[0]
            if not href.startswith("http"):
                href = "https://www.bankier.pl" + href

            espi_url    = _find_espi_link(item, href)
            raw_company = _extract_company_raw(title, item)
            clean_title = _clean_title(title)

            ticker_hint = _extract_ticker_hint(item)

            # Slow path: dla spółek nieznanych match_ticker() — fetch strony ogłoszenia
            if ticker_hint is None:
                from utils.company import match_ticker as _match
                if _match(clean_title, raw_company) is None:
                    ticker_hint = _fetch_ticker_from_announcement(href)
                    if ticker_hint:
                        logger.debug(f"Ticker via slow-path: {raw_company!r} → {ticker_hint}")

            company = get_folder_name(
                title       = clean_title,
                company_raw = raw_company,
                ticker_hint = ticker_hint,
            )

            pub_time = parsed_dt.time() if parsed_dt else None

            results.append({
                "title":       clean_title,
                "url":         espi_url,
                "bankier_url": href,
                "company":     company,
                "date":        parsed_dt.date() if parsed_dt else since.date(),
                "pub_time":    pub_time,
                "source":      "bankier",
            })

        # Jeśli strona miała starsze items ALE też targetowe — idziemy dalej (interleaved).
        # Jeśli strona była w całości starsza — stop.
        if found_older and not page_had_target:
            break

    if not results:
        logger.warning(
            f"Bankier: ZERO ogłoszeń od {since} — "
            f"items={diag['items_found']}, no_date={diag['no_date']}, "
            f"older={diag['older']}, future={diag['future']}, "
            f"unparseable={diag['unparseable']}, sample={sample_date!r}. "
            f"Sprawdź: {BANKIER_BASE_URL.format(page=1)}"
        )
    else:
        logger.info(f"Bankier: znaleziono {len(results)} ogłoszeń od {since}")

    return results


def _find_espi_link(item, bankier_url: str) -> str:
    for a in item.find_all("a", href=True):
        href = a["href"]
        if "espi.com.pl" in href or "pap.pl/node" in href:
            return href
    return bankier_url


def _extract_company_raw(title: str, item) -> str:
    for sel in [
        ".m-quotes-announcements-item__company",
        ".company", "[class*='company']",
        "strong", "b",
    ]:
        el = item.select_one(sel)
        if el:
            text = el.get_text(strip=True)
            if 2 < len(text) < 80:
                return _clean(text)

    for sep in (": ", " — ", " – ", " - "):
        if sep in title:
            candidate = title.split(sep)[0].strip()
            if 2 < len(candidate) < 80:
                return _clean(candidate)

    return _clean(title[:40])


def _clean_title(title: str) -> str:
    return re.sub(r'\s+', ' ', title).strip()


def _clean(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    return re.sub(r'\s+', ' ', name).strip()[:80]


def _extract_ticker_hint(item) -> str | None:
    """Wyciąga ticker GPW z linku profilu spółki (?symbol=XYZ) w elemencie listy."""
    from urllib.parse import parse_qs, urlparse
    for a in item.find_all("a", href=True):
        href = a["href"]
        if "profile/quote.html" not in href:
            continue
        if not href.startswith("http"):
            href = "https://www.bankier.pl" + href
        symbol = parse_qs(urlparse(href).query).get("symbol", [None])[0]
        if symbol and len(symbol) <= 6:
            return symbol.upper()
    return None


def _fetch_ticker_from_announcement(announcement_url: str) -> str | None:
    """
    Slow-path: dla spółek nieznanych match_ticker() (zwykle NewConnect).
    Wykonuje 2 żądania HTTP:
      1. Strona ogłoszenia → Bankier company symbol
      2. Strona profilu spółki → ticker GPW z nagłówka "(XYZ)"
    Wywołany tylko gdy match_ticker() zwróci None.
    """
    from urllib.parse import parse_qs, urlparse
    import re as _re

    resp = get(announcement_url)
    if resp is None:
        return None
    soup = BeautifulSoup(resp.text, "html5lib")

    bankier_symbol = None
    anchor = soup.select_one("a.m-quote-list__anchor[href*='profile/quote.html']")
    if anchor is None:
        anchor = soup.select_one("a[href*='profile/quote.html']")
    if anchor:
        href_raw = anchor["href"]
        href = href_raw if isinstance(href_raw, str) else href_raw[0]
        if not href.startswith("http"):
            href = "https://www.bankier.pl" + href
        sym = parse_qs(urlparse(href).query).get("symbol", [None])[0]
        if sym:
            bankier_symbol = sym.upper()

    if not bankier_symbol:
        return None

    profile_url = f"https://www.bankier.pl/inwestowanie/profile/quote.html?symbol={bankier_symbol}"
    resp2 = get(profile_url)
    if resp2 is None:
        return None
    soup2 = BeautifulSoup(resp2.text, "html5lib")

    heading = soup2.select_one(".a-heading__suffix")
    if heading:
        text = heading.get_text(strip=True)
        m = _re.search(r'\(([A-Z]{2,6})\)\s*$', text)
        if m:
            ticker = m.group(1)
            full_name = _re.sub(r'\s*\([A-Z]{2,6}\)\s*$', '', text).strip()
            if full_name:
                _save_display_name(ticker, full_name)
            return ticker

    return None


def _save_display_name(ticker: str, display_name: str) -> None:
    """Auto-zapisuje ticker → pełna nazwa do data/ticker_display_names.json."""
    import json as _json
    from pathlib import Path as _Path
    from utils.gpw_tickers import _load_display_names

    path = _Path(__file__).resolve().parent.parent / "data" / "ticker_display_names.json"
    try:
        existing = dict(_load_display_names())
        if existing.get(ticker) == display_name:
            return
        existing[ticker] = display_name
        path.write_text(
            _json.dumps(dict(sorted(existing.items())), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _load_display_names.cache_clear()
    except Exception:
        pass  # non-critical
