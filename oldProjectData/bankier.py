"""
Scraper Bankier.pl ESPI/EBI — GŁÓWNE źródło ogłoszeń.
Używa get_folder_name() do matchowania tytułu ogłoszenia do tickera GPW.
"""
import logging
import re
from datetime import date, datetime

from bs4 import BeautifulSoup

from config import BANKIER_BASE_URL, MAX_PAGES_BANKIER
from scraper.base import get
from utils.company import get_folder_name

logger = logging.getLogger(__name__)


def _parse_date(text: str) -> date | None:
    text = text.strip()
    for fmt in ("%d-%m-%Y %H:%M", "%d-%m-%Y", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
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


def _parse_dt(text: str) -> datetime | None:
    """Parsuje datę + godzinę. Zwraca None gdy brak godziny."""
    text = text.strip()
    for fmt in ("%d-%m-%Y %H:%M", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def collect_bankier(target_date: date) -> list[dict]:
    results     = []
    found_older = False

    # Diagnostyka 2026-05-20: gdy result==0 musimy odróżnić CDN stale cache
    # (older=N, sample='YYYY-MM-DD') od HTML structure change (no_date=N) od
    # genuine empty page (items=0 → 'brak elementów'). Bez tych liczników
    # finalny WARNING był zbyt ogólny — patrz prod-intraday-premarket-job-zj66p.
    diag = {"items_found": 0, "no_date": 0, "older": 0, "future": 0, "unparseable": 0}
    sample_date: str | None = None

    for page in range(1, MAX_PAGES_BANKIER + 1):
        url = BANKIER_BASE_URL.format(page=page)
        logger.info(f"Bankier strona {page}: {url}")

        resp = get(url)
        if resp is None:
            # Transient: get() already warned with retry details. No Sentry mail.
            logger.warning(f"Nie można pobrać strony {page}")
            break

        soup  = BeautifulSoup(resp.text, "html5lib")
        items = (
            soup.select(".m-quotes-announcements-item") or
            soup.select(".listingItem") or
            soup.select("li[class*='item']")
        )

        if not items:
            logger.warning(f"Bankier strona {page}: brak elementów")
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
                if sample_date is None:
                    sample_date = "<no date_el>"
                continue

            raw_date = (
                date_el.get("datetime") or
                date_el.get("content") or
                date_el.get_text(strip=True)
            )
            raw_date_str = raw_date[:20]
            parsed_dt    = _parse_dt(raw_date_str)
            if parsed_dt:
                item_date = parsed_dt.date()
                item_time = parsed_dt.time()
            else:
                item_date = _parse_date(raw_date_str)
                item_time = None
            if item_date is None:
                diag["unparseable"] += 1
                if sample_date is None:
                    sample_date = f"<unparseable:{raw_date_str!r}>"
                continue

            if item_date > target_date:
                diag["future"] += 1
                if sample_date is None:
                    sample_date = str(item_date)
                continue
            if item_date < target_date:
                # FIX 2026-04-22: continue zamiast break — nie zakładamy chronologii.
                # Bug: jeden interleaved older item wywalał OUTER loop i pomijał
                # następne target_date itemy. found_older nadal sygnalizuje że
                # przekroczyliśmy granicę dat — OUTER decyduje na podstawie
                # page_had_target czy warto iść na następną stronę.
                found_older = True
                diag["older"] += 1
                if sample_date is None:
                    sample_date = str(item_date)
                continue

            page_had_target = True

            link_el = item.select_one("a[href]")
            if not link_el:
                continue

            title = link_el.get_text(strip=True)
            href_raw = link_el["href"]
            href     = href_raw if isinstance(href_raw, str) else href_raw[0]
            if not href.startswith("http"):
                href = "https://www.bankier.pl" + href

            espi_url    = _find_espi_link(item, href)
            raw_company = _extract_company_raw(title, item)
            clean_title = _clean_title(title)

            # Fast path: profile link embedded in list item (currently not present
            # in Bankier HTML, kept for future-proofing)
            ticker_hint = _extract_ticker_hint(item)

            # Slow path: for unknown companies (match_ticker=None) fetch the
            # announcement page → profile page to get GPW ticker (e.g. DRG for DRAGEUS)
            if ticker_hint is None:
                from utils.company import match_ticker as _match
                if _match(clean_title, raw_company) is None:
                    ticker_hint = _fetch_ticker_from_announcement(href)
                    if ticker_hint:
                        logger.debug(f"Fetched ticker via announcement page: {raw_company!r} → {ticker_hint}")

            company = get_folder_name(
                title       = clean_title,
                company_raw = raw_company,
                ticker_hint = ticker_hint,
            )

            results.append({
                "title":       _clean_title(title),
                "url":         espi_url,
                "bankier_url": href,
                "company":     company,
                "date":        item_date,
                "pub_time":    item_time,   # datetime.time lub None
                "source":      "bankier",
            })

        if found_older and not page_had_target:
            break
        if found_older:
            break

    if not results:
        # Atrybucja powodu — kluczowa diagnostyka rozróżniająca:
        #   older=N         → Bankier CDN zwrócił stale snapshot (najczęstsze)
        #   no_date=N       → HTML structure change (selektory matchują, ale brak <time>)
        #   unparseable=N   → format daty się zmienił
        #   future=N        → bardzo rzadkie (jutrzejszy snapshot)
        #   items_found=0   → już zalogowane jako "brak elementów" wcześniej
        logger.warning(
            f"Bankier: ZERO ogłoszeń z {target_date} — "
            f"items={diag['items_found']}, no_date={diag['no_date']}, "
            f"older={diag['older']}, future={diag['future']}, "
            f"unparseable={diag['unparseable']}, sample={sample_date!r}. "
            f"Sprawdź ręcznie: {BANKIER_BASE_URL.format(page=1)}"
        )
    else:
        logger.info(f"Bankier: znaleziono {len(results)} ogłoszeń z {target_date}")
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
        "strong", "b"
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
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:80]


def _extract_ticker_hint(item) -> str | None:
    """Extract GPW ticker from company profile link in Bankier announcement item.

    Bankier embeds <a href="/inwestowanie/profile/quote.html?symbol=DRG"> in each
    item — the ?symbol= param is the actual GPW ticker, available without extra HTTP.
    Only symbols ≤ 6 chars are treated as real ticker codes (longer = name abbreviation).
    """
    from urllib.parse import parse_qs, urlparse

    for a in item.find_all("a", href=True):
        href = a["href"]
        if "profile/quote.html" not in href:
            continue
        if not href.startswith("http"):
            href = "https://www.bankier.pl" + href
        params = parse_qs(urlparse(href).query)
        symbol = params.get("symbol", [None])[0]
        if symbol and len(symbol) <= 6:
            return symbol.upper()
    return None


def _fetch_ticker_from_announcement(announcement_url: str) -> str | None:
    """For NewConnect companies unknown to match_ticker(): fetch announcement page
    to get Bankier company symbol, then fetch company profile page to extract the
    real GPW ticker from the heading (e.g. 'Drageus Games SA (DRG)' → 'DRG').

    Requires 2 HTTP requests — only called when match_ticker() returns None.
    Only tickers ≤ 6 chars are accepted (longer = likely a company name abbreviation).
    """
    from urllib.parse import parse_qs, urlparse

    # Step 1: announcement page → Bankier company symbol
    resp = get(announcement_url)
    if resp is None:
        return None
    soup = BeautifulSoup(resp.text, "html5lib")

    # Targeted selector: <a class="m-quote-list__anchor -stock" href="...?symbol=DRAGEUS">
    # inside section.o-espi-ebi-quote-box — the company quote box on the announcement page.
    # Fallback: any a[href*="profile/quote.html"] (first match = company link).
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

    # Step 2: company profile page → GPW ticker from heading "(DRG)"
    profile_url = f"https://www.bankier.pl/inwestowanie/profile/quote.html?symbol={bankier_symbol}"
    resp2 = get(profile_url)
    if resp2 is None:
        return None
    soup2 = BeautifulSoup(resp2.text, "html5lib")

    heading = soup2.select_one(".a-heading__suffix")
    if heading:
        text = heading.get_text(strip=True)
        m = re.search(r'\(([A-Z]{2,6})\)\s*$', text)
        if m:
            ticker = m.group(1)
            # Auto-zapisz pełną nazwę do ticker_display_names.json (przed nawiasem)
            full_name = re.sub(r'\s*\([A-Z]{2,6}\)\s*$', '', text).strip()
            if full_name:
                _save_display_name(ticker, full_name)
            return ticker

    return None


def _save_display_name(ticker: str, display_name: str) -> None:
    """Zapisuje ticker → pełna nazwa do data/ticker_display_names.json (auto-update)."""
    import json as _json
    from pathlib import Path as _Path

    from utils.gpw_tickers import _load_display_names

    path = _Path(__file__).resolve().parent.parent / "data" / "ticker_display_names.json"
    try:
        existing = dict(_load_display_names())
        if existing.get(ticker) == display_name:
            return  # bez zmian
        existing[ticker] = display_name
        path.write_text(
            _json.dumps(dict(sorted(existing.items())), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _load_display_names.cache_clear()
    except Exception:
        pass  # non-critical — xpost nie wysypuje się przez brak display name