"""
bankier_html_check.py — F-01 Phase 1
Weryfikuje selektory CSS z bankier.py na żywej stronie Bankier.pl.
Standalone: brak importów z oldProjectData.
"""

import time
import sys
from urllib.parse import urlparse, parse_qs

import httpx
from bs4 import BeautifulSoup

URL = "https://www.bankier.pl/gielda/wiadomosci/komunikaty-spolek"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ─── selektor listy (próbujemy w kolejności jak w bankier.py) ────────────────
ITEM_SELECTORS = [
    ".m-quotes-announcements-item",
    ".listingItem",
    "li[class*='item']",
]

DATE_SELECTORS = (
    ".m-quotes-announcements-item__date, .date, time, [class*='date']"
)
COMPANY_SELECTORS = (
    ".m-quotes-announcements-item__company, .company, [class*='company'], strong"
)
TICKER_SELECTOR = "a[href*='profile/quote.html']"
LINK_SELECTOR = "a[href]"


def find_active_item_selector(soup: BeautifulSoup) -> tuple[str | None, list]:
    for sel in ITEM_SELECTORS:
        items = soup.select(sel)
        if items:
            return sel, items
    return None, []


def extract_ticker(item: BeautifulSoup) -> str:
    anchor = item.select_one(TICKER_SELECTOR)
    if not anchor:
        return "—"
    href = anchor.get("href", "")
    parsed = urlparse(href)
    params = parse_qs(parsed.query)
    return params.get("symbol", ["—"])[0]


def first_announcement_url(item: BeautifulSoup) -> str:
    for a in item.select(LINK_SELECTOR):
        href = a.get("href", "")
        if href and ("espi" in href or "komunikat" in href or href.startswith("http")):
            return href
    # fallback: każdy link w item
    a = item.select_one(LINK_SELECTOR)
    return a.get("href", "—") if a else "—"


def run():
    print(f"[bankier_html_check] Fetching: {URL}\n")
    time.sleep(0.5)

    try:
        resp = httpx.get(URL, headers=HEADERS, timeout=20, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"[ERROR] HTTP request failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Status: {resp.status_code}  |  Content-Type: {resp.headers.get('content-type', '?')}")
    print(f"Body size: {len(resp.content):,} bytes\n")

    soup = BeautifulSoup(resp.text, "html5lib")

    # ── 1. Item selectors ────────────────────────────────────────────────────
    print("=" * 60)
    print("1. ITEM SELECTORS")
    print("=" * 60)
    active_selector, items = find_active_item_selector(soup)
    if not active_selector:
        print("[WARN] Żaden selektor listy nie trafił. Sprawdź ręcznie DevTools.")
        for sel in ITEM_SELECTORS:
            print(f"  {sel!r:50s} → 0 elementów")
        sys.exit(1)

    for sel in ITEM_SELECTORS:
        count = len(soup.select(sel))
        flag = "  << AKTYWNY" if sel == active_selector else ""
        print(f"  {sel!r:50s} -> {count} elementow{flag}")
    print()

    # ── 2. Dane pierwszego itemu ──────────────────────────────────────────────
    print("=" * 60)
    print("2. DANE PIERWSZEGO ITEMU")
    print("=" * 60)
    first = items[0]

    # Title — szukamy tekstu kotwicy lub h-tag
    title_el = first.find(["h2", "h3", "h4", "a"])
    title = title_el.get_text(strip=True)[:120] if title_el else "—"
    print(f"  title   : {title}")

    # Date
    date_el = first.select_one(DATE_SELECTORS)
    date_raw = date_el.get_text(strip=True) if date_el else "—"
    print(f"  date    : {date_raw}")

    # Company
    company_el = first.select_one(COMPANY_SELECTORS)
    company = company_el.get_text(strip=True)[:80] if company_el else "—"
    print(f"  company : {company}")

    # Ticker
    ticker = extract_ticker(first)
    print(f"  ticker  : {ticker}")

    # URL
    ann_url = first_announcement_url(first)
    print(f"  url     : {ann_url}")
    print()

    # ── 3. Pełna lista — pierwsze 5 itemów ───────────────────────────────────
    print("=" * 60)
    print(f"3. PIERWSZE 5 Z {len(items)} ITEMÓW")
    print("=" * 60)
    for i, item in enumerate(items[:5], 1):
        t_el = item.find(["h2", "h3", "h4", "a"])
        t = t_el.get_text(strip=True)[:80] if t_el else "—"
        d_el = item.select_one(DATE_SELECTORS)
        d = d_el.get_text(strip=True) if d_el else "—"
        c_el = item.select_one(COMPANY_SELECTORS)
        c = c_el.get_text(strip=True)[:40] if c_el else "—"
        tk = extract_ticker(item)
        print(f"  [{i}] {t}")
        print(f"       date={d!r}  company={c!r}  ticker={tk!r}")
    print()

    # ── 4. Date selectors diagnostics ────────────────────────────────────────
    print("=" * 60)
    print("4. DATE SELECTOR DIAGNOSTICS (wszystkie itemy)")
    print("=" * 60)
    found_with_date = sum(1 for it in items if it.select_one(DATE_SELECTORS))
    print(f"  Itemy z datą: {found_with_date}/{len(items)}")
    print()

    # ── 5. HTML dump pierwszego itemu (dla research.md) ─────────────────────
    print("=" * 60)
    print("5. HTML DUMP PIERWSZEGO ITEMU (classes only)")
    print("=" * 60)
    # Wypisz klasy wszystkich elementow w pierwszym item
    for el in first.find_all(True):
        classes = el.get("class", [])
        text_preview = el.get_text(strip=True)[:40].encode("ascii", "replace").decode()
        if classes:
            print(f"  <{el.name} class={classes}> text={text_preview!r}")
    print()

    # ── 6. Wynik ─────────────────────────────────────────────────────────────
    print("=" * 60)
    print("6. WYNIK")
    print("=" * 60)
    print(f"  Aktywny selektor : {active_selector!r}")
    print(f"  Liczba elementów : {len(items)}")
    print(f"  Pierwsze title   : {title}")
    print()

    if len(items) >= 1:
        print("[OK] Znaleziono >=1 element. Kryterium automatyczne spelnione.")
        sys.exit(0)
    else:
        print("[FAIL] Brak elementow.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run()
