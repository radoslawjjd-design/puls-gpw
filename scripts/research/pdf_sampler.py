"""
pdf_sampler.py — F-01 Phase 2
Pobiera 5-10 ogloszen ESPI/EBI z Bankier.pl, szuka PDF-ow,
klasyfikuje je: TEXT / SCAN / ENCRYPTED / NO_PDF.
Standalone: brak importow z oldProjectData.
"""

import io
import sys
import time

import httpx
import pypdf
from bs4 import BeautifulSoup

LISTING_URL = "https://www.bankier.pl/gielda/wiadomosci/komunikaty-spolek"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Pomijaj PDF-y ktore sa dokumentami regulaminowymi / cookies itp.
BLOCKED_KEYWORDS = ["regulamin", "cookies", "polityka", "rodo", "statut"]

MAX_ANNOUNCEMENTS = 10
MIN_TEXT_CHARS = 100  # minimalna dlugosc tekstu zeby uznac PDF za TEXT (nie SCAN)


def is_blocked(url: str) -> bool:
    lower = url.lower()
    return any(kw in lower for kw in BLOCKED_KEYWORDS)


def get_soup(client: httpx.Client, url: str) -> BeautifulSoup | None:
    time.sleep(0.5)
    try:
        resp = client.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html5lib")
    except httpx.HTTPError as exc:
        print(f"  [HTTP ERR] {url}: {exc}")
        return None


def find_pdf_link(soup: BeautifulSoup, base_url: str) -> str | None:
    """Znajdz pierwszy link do PDF na stronie ogloszenia."""
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.lower().endswith(".pdf"):
            continue
        if is_blocked(href):
            continue
        # Normalizuj URL
        if href.startswith("http"):
            return href
        elif href.startswith("/"):
            from urllib.parse import urlparse
            parsed = urlparse(base_url)
            return f"{parsed.scheme}://{parsed.netloc}{href}"
        else:
            return href
    return None


def classify_pdf(client: httpx.Client, pdf_url: str) -> tuple[str, int, str]:
    """
    Pobierz PDF i klasyfikuj.
    Zwraca: (klasyfikacja, rozmiar_KB, sample_tekstu)
    """
    time.sleep(0.5)
    try:
        resp = client.get(pdf_url, headers=HEADERS, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return "HTTP_ERR", 0, str(exc)[:50]

    data = resp.content
    size_kb = len(data) // 1024

    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return "ENCRYPTED", size_kb, ""
        text = ""
        for page in reader.pages[:3]:  # max 3 strony dla szybkosci
            text += page.extract_text() or ""
        text = text.strip()
        if len(text) >= MIN_TEXT_CHARS:
            sample = text[:50].encode("ascii", "replace").decode().replace("\n", " ")
            return "TEXT", size_kb, sample
        else:
            return "SCAN", size_kb, text[:50].encode("ascii", "replace").decode()
    except Exception as exc:
        return "ENCRYPTED", size_kb, str(exc)[:50]


def extract_ticker_from_title(title: str) -> str:
    """Wyciagnij kod ESPI z poczatku tytulu np. 'SFKPOLKAP: Naozenie...' -> 'SFKPOLKAP'"""
    if ":" in title:
        code = title.split(":")[0].strip()
        if code and len(code) <= 20 and code.isupper():
            return code
    return title[:15]


def run():
    results = []

    with httpx.Client() as client:
        # 1. Pobierz liste ogloszen
        print(f"[pdf_sampler] Pobieranie listy: {LISTING_URL}")
        soup = get_soup(client, LISTING_URL)
        if not soup:
            print("[ERROR] Nie mozna pobrac strony listingowej.", file=sys.stderr)
            sys.exit(1)

        items = soup.select(".m-quotes-announcements-item")
        print(f"[pdf_sampler] Znaleziono {len(items)} ogloszen, biore pierwsze {MAX_ANNOUNCEMENTS}\n")

        announcement_urls = []
        for item in items[:MAX_ANNOUNCEMENTS]:
            a = item.select_one(".m-quotes-announcements-item__anchor")
            if not a:
                a = item.select_one("a[href]")
            if a and a.get("href"):
                href = a["href"]
                if not href.startswith("http"):
                    href = "https://www.bankier.pl" + href
                announcement_urls.append((extract_ticker_from_title(a.get_text(strip=True)), href))

        print(f"Zebranych URL-i ogloszen: {len(announcement_urls)}\n")

        # 2. Dla kazdego ogloszenia: szukaj PDF
        for idx, (ticker, ann_url) in enumerate(announcement_urls, 1):
            print(f"[{idx:2d}/{len(announcement_urls)}] {ticker[:20]:<20} {ann_url[:70]}")

            ann_soup = get_soup(client, ann_url)
            if not ann_soup:
                results.append((ticker, ann_url, "—", "HTTP_ERR", 0, ""))
                continue

            pdf_url = find_pdf_link(ann_soup, ann_url)
            if not pdf_url:
                print(f"         -> NO_PDF")
                results.append((ticker, ann_url, "—", "NO_PDF", 0, ""))
                continue

            print(f"         -> PDF: {pdf_url[:70]}")
            klasyfikacja, size_kb, sample = classify_pdf(client, pdf_url)
            print(f"         -> {klasyfikacja} ({size_kb} KB)  sample={sample!r}")
            results.append((ticker, ann_url, pdf_url, klasyfikacja, size_kb, sample))

    # 3. Tabela wynikow
    print()
    print("=" * 80)
    print("TABELA WYNIKOW")
    print("=" * 80)
    print(f"{'Ticker':<20} {'Rozm.KB':>7} {'Klas.':<12} {'Sample (50 zn.)'}")
    print("-" * 80)
    for (ticker, ann_url, pdf_url, klas, size_kb, sample) in results:
        print(f"{ticker[:20]:<20} {size_kb:>7} {klas:<12} {sample!r}")

    # 4. Podsumowanie
    counts = {"TEXT": 0, "SCAN": 0, "ENCRYPTED": 0, "NO_PDF": 0, "HTTP_ERR": 0}
    for r in results:
        klas = r[3]
        counts[klas] = counts.get(klas, 0) + 1

    print()
    print("=" * 80)
    print("PODSUMOWANIE")
    print("=" * 80)
    total = len(results)
    for klas, count in counts.items():
        pct = (count / total * 100) if total else 0
        print(f"  {klas:<12}: {count:>2} / {total}  ({pct:.0f}%)")
    print()

    # OCR decision hint
    scan_pct = (counts["SCAN"] / total * 100) if total else 0
    if scan_pct > 20:
        print(f"[WARN] {scan_pct:.0f}% PDF-ow to skany — ryzyko dla S-02, OCR moze byc potrzebne.")
    else:
        print(f"[OK] Skany: {scan_pct:.0f}% (<= 20%) — HTML fallback wystarczy, OCR niepotrzebne w MVP.")

    if total >= 5:
        print("[OK] Wyniki dla >=5 ogloszen. Kryterium automatyczne spelnione.")
        sys.exit(0)
    else:
        print(f"[FAIL] Tylko {total} ogloszen < 5.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run()
