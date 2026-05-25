"""
Parser treści ogłoszeń ESPI/EBI.

Dwa etapy:
1. discover_announcement(ann)  — wchodzi na stronę, zwraca listę plików do pobrania
2. fetch_files(discovered, ann) — pobiera dane równolegle (ThreadPoolExecutor)
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scraper.base import download_binary, get

logger = logging.getLogger(__name__)

PDF_EXTENSIONS   = {".pdf"}
XHTML_EXTENSIONS = {".xhtml", ".xhtm", ".xbrl"}

BLOCKED_FILENAME_KEYWORDS = [
    "regulamin", "polityka_prywatnosci", "polityka_plikow",
    "cookies", "privacy_policy", "terms_of_service",
]

FETCH_WORKERS = 4

# ── Fast-path dla wyników finansowych KNF (2026-04-23) ────────────────────────
# Bug: ENELMED/IMS/SELENAFM/BORYSZEW timeoutowały bo 17-19 załączników na spółkę
# (sprawozdania, audyty, opinie RN, schematy XBRL) → 17 × 120s × 3 retry =
# kilkadziesiąt minut na jedną spółkę → job timeout 1800s.
#
# Rozwiązanie (decyzja user 2026-04-23): dla raportów okresowych KNF używaj
# WYŁĄCZNIE table.seauid2 z bankier (zawiera WYBRANE DANE FINANSOWE 2025 vs 2024:
# Przychody/EBIT/EBITDA/Zysk netto/Aktywa) — wystarczy dla sentiment analizy.
# Pozostałe załączniki (audyt, RN, XBRL, sprawozdania) — SKIP całkowicie.

# Tytuły kwalifikujące się do fast-path (KNF raporty okresowe)
_FINANCIAL_TITLE_PATTERNS = [
    re.compile(r"\bwyniki\s+finansowe\b", re.IGNORECASE),
    re.compile(r"\braport\s+(okresowy|roczny|polroczny|kwartalny)\b", re.IGNORECASE),
    re.compile(r"\b(RR|SRR|PSr|QSr)\b", re.IGNORECASE),
]

# Min. długość treści tabeli seauid2 żeby uznać za sensowną finansową
_SEAUID_MIN_TEXT_LEN = 800

# Keywords WYMAGANE w tabeli seauid (żeby odróżnić dane finansowe od headera KNF)
_SEAUID_FINANCIAL_KEYWORDS = ["przychody", "ebit", "zysk", "aktywa", "kapital", "kapitał"]


def is_financial_results_title(title: str) -> bool:
    """Heurystyka: czy tytuł ogłoszenia wskazuje na raport okresowy KNF.

    Triggeruje fast-path z table.seauid2. Match: "Wyniki finansowe",
    "Raport okresowy/roczny/półroczny/kwartalny", lub typy KNF (RR/SRR/PSr/QSr).
    """
    if not title:
        return False
    return any(p.search(title) for p in _FINANCIAL_TITLE_PATTERNS)


def extract_seauid_financial_data(soup: BeautifulSoup) -> str | None:
    """Wyciąga `table.seauid2` z bankier zawierający WYBRANE DANE FINANSOWE.

    Zwraca markdown-friendly tekst (linie z |) lub None gdy:
    - brak tabel seauid
    - tabela < 800 znaków (smell test)
    - tabela bez słów kluczowych (Przychody/EBIT/Zysk/Aktywa/Kapitał) — to header KNF
    """
    tables = soup.find_all("table", class_=lambda c: c and "seauid" in (c if isinstance(c, str) else " ".join(c)))
    for table in tables:
        text = table.get_text(" | ", strip=True)
        if len(text) < _SEAUID_MIN_TEXT_LEN:
            continue
        text_lower = text.lower()
        if not any(kw in text_lower for kw in _SEAUID_FINANCIAL_KEYWORDS):
            continue
        # Match — zwracaj sformatowany tekst (już ma | jako separator kolumn)
        return text
    return None


# ── ETAP 1: Odkryj pliki ──────────────────────────────────────────────────────

def discover_announcement(announcement: dict) -> list[dict]:
    """
    Wchodzi na stronę ogłoszenia i zwraca listę plików do pobrania.
    NIE pobiera zawartości — tylko metadane.
    Nazwa firmy jest już znormalizowana w announcement["company"].
    """
    url = announcement["url"]
    logger.info(f"Analizuję: {url}")

    resp = get(url)
    if resp is None:
        # Transient: get() already warned with retry details. Duplicate noise w/o Sentry mail.
        logger.warning(f"Nie można pobrać: {url}")
        return []

    soup      = BeautifulSoup(resp.text, "html5lib")
    final_url = resp.url

    # FAST-PATH (2026-04-23): wyniki finansowe KNF → tylko table.seauid2,
    # skip 12-19 załączników (audyt/RN/XBRL/sprawozdania). Saved 17×120s timeouts.
    title_str = announcement.get("title", "")
    if is_financial_results_title(title_str):
        seauid_text = extract_seauid_financial_data(soup)
        if seauid_text:
            logger.info(
                f"  Fast-path KNF: table.seauid2 ({len(seauid_text)} znaków) — "
                f"pomijam załączniki PDF/XHTML (Sentry timeout fix)"
            )
            return [{
                "file_url":     final_url,
                "filename":     _safe_filename(announcement, final_url, is_text=True),
                "content_type": "txt",
                "is_text":      True,
                "_soup":        soup,
                "_seauid_text": seauid_text,
            }]
        # Observability (2026-04-23): title match BUT seauid empty → fallback
        # do klasycznego flow z załącznikami. Loguj WARNING z diagnostyką żeby
        # zrozumieć dlaczego seauid nie został wyciągnięty (BOOMBIT-SRR/RANKPROGR-SRR
        # edge case z dzisiejszego rerun).
        seauid_tables = soup.find_all(
            "table",
            class_=lambda c: c and "seauid" in (c if isinstance(c, str) else " ".join(c)),
        )
        seauid_diag = (
            f"tables_found={len(seauid_tables)}, "
            f"longest_text={max((len(t.get_text(strip=True)) for t in seauid_tables), default=0)}, "
            f"page_size={len(resp.text)}"
        )
        logger.warning(
            f"  Fast-path KNF MISS: title='{title_str[:60]}' match=True ALE seauid empty/short. "
            f"Diag: {seauid_diag}. Fallback do klasycznego flow z załącznikami."
        )

    if "bankier.pl" in final_url:
        espi_url = _find_espi_redirect(soup, final_url)
        if espi_url and espi_url != final_url:
            logger.info(f"  Przekierowanie do ESPI: {espi_url}")
            resp2 = get(espi_url)
            if resp2:
                soup      = BeautifulSoup(resp2.text, "html5lib")
                final_url = espi_url

    discovered = []

    pdf_links = _find_attachments(soup, final_url, PDF_EXTENSIONS)
    for file_url in pdf_links:
        logger.info(f"  PDF: {file_url}")
        discovered.append({
            "file_url":     file_url,
            "filename":     _safe_filename(announcement, file_url),
            "content_type": "pdf",
            "is_text":      False,
        })

    xhtml_links = _find_attachments(soup, final_url, XHTML_EXTENSIONS)
    for file_url in xhtml_links:
        ext   = file_url.lower().rsplit(".", 1)[-1]
        ctype = "xbrl" if ext == "xbrl" else "xhtml"
        logger.info(f"  {ctype.upper()}: {file_url}")
        discovered.append({
            "file_url":     file_url,
            "filename":     _safe_filename(announcement, file_url),
            "content_type": ctype,
            "is_text":      False,
        })

    if not discovered:
        logger.info("  Brak załączników — zaplanowano ekstrakcję tekstu")
        discovered.append({
            "file_url":     final_url,
            "filename":     _safe_filename(announcement, final_url, is_text=True),
            "content_type": "txt",
            "is_text":      True,
            "_soup":        soup,
        })

    return discovered


# ── ETAP 2: Pobierz dane równolegle ───────────────────────────────────────────

def fetch_files(discovered: list[dict], announcement: dict) -> list[dict]:
    text_items   = [item for item in discovered if item.get("is_text")]
    binary_items = [item for item in discovered if not item.get("is_text")]

    files = []

    for item in text_items:
        result = _fetch_single(item, announcement)
        if result:
            files.append(result)

    if binary_items:
        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as executor:
            futures = {
                executor.submit(_fetch_single, item, announcement): item
                for item in binary_items
            }
            results = {}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    result = future.result()
                    if result:
                        results[item["file_url"]] = result
                except Exception as e:
                    logger.error(f"  Błąd pobierania {item['file_url']}: {e}")

            for item in binary_items:
                if item["file_url"] in results:
                    files.append(results[item["file_url"]])

    return files


def _fetch_single(item: dict, announcement: dict) -> dict | None:
    content_type = item["content_type"]
    is_text      = item.get("is_text", False)
    file_url     = item["file_url"]

    if is_text:
        # FAST-PATH KNF (2026-04-23): jeśli mamy seauid_text, użyj go bezpośrednio
        # — to są już wyciągnięte WYBRANE DANE FINANSOWE, kompletny content.
        seauid_text = item.get("_seauid_text")
        if seauid_text:
            return {
                "content_type": "txt",
                "filename":     item["filename"],
                "data":         f"WYBRANE DANE FINANSOWE (z table.seauid2 bankier):\n\n{seauid_text}",
                "company":      announcement["company"],
                "title":        announcement["title"],
                "date":         announcement["date"],
                "source":       announcement["source"],
            }

        soup = item.get("_soup")
        if soup is None:
            resp = get(file_url)
            if resp is None:
                logger.warning(f"  Nie można pobrać strony: {file_url}")
                return None
            soup = BeautifulSoup(resp.text, "html5lib")

        text = _extract_text(soup, file_url)
        if text and len(text) > 80:
            return {
                "content_type": "txt",
                "filename":     item["filename"],
                "data":         text,
                "company":      announcement["company"],  # już znormalizowana
                "title":        announcement["title"],
                "date":         announcement["date"],
                "source":       announcement["source"],
            }
        logger.warning(f"  Nie udało się wyciągnąć treści z: {file_url}")
        return None
    else:
        min_size = 1000 if content_type == "pdf" else 500
        data = download_binary(file_url)
        if data and len(data) > min_size:
            return {
                "content_type": content_type,
                "filename":     item["filename"],
                "data":         data,
                "company":      announcement["company"],  # już znormalizowana
                "title":        announcement["title"],
                "date":         announcement["date"],
                "source":       announcement["source"],
            }
        logger.warning(f"  Plik zbyt mały lub pusty: {file_url}")
        return None


def parse_announcement(announcement: dict) -> list[dict]:
    """Kompatybilność wsteczna."""
    discovered = discover_announcement(announcement)
    return fetch_files(discovered, announcement)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_espi_redirect(soup: BeautifulSoup, base_url: str) -> str | None:
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(domain in href for domain in ["espi.com.pl", "pap.pl/node", "knf.gov.pl"]):
            if not href.startswith("http"):
                href = urljoin(base_url, href)
            return href

    for meta in soup.find_all("meta", attrs={"http-equiv": "refresh"}):
        content = meta.get("content", "")
        m = re.search(r'url=(.+)', content, re.IGNORECASE)
        if m:
            url = m.group(1).strip("'\"")
            if "espi" in url or "pap.pl" in url:
                return url
    return None


def _find_attachments(soup: BeautifulSoup, base_url: str, extensions: set) -> list[str]:
    found = []
    for a in soup.find_all("a", href=True):
        href       = a["href"].strip()
        lower_path = href.lower().split("?")[0]

        if not any(lower_path.endswith(ext) for ext in extensions):
            continue

        if not href.startswith("http"):
            href = urljoin(base_url, href)

        filename = href.lower().split("/")[-1].split("?")[0]
        if any(kw in filename for kw in BLOCKED_FILENAME_KEYWORDS):
            continue

        if href not in found:
            found.append(href)
    return found


def _extract_text(soup: BeautifulSoup, url: str) -> str:
    for tag in soup.select(
        "nav, header, footer, script, style, noscript, "
        ".ad, .menu, .sidebar, [class*='banner'], "
        "[class*='cookie'], [class*='footer'], [class*='header']"
    ):
        tag.decompose()

    content_selectors = [
        "#emitent", ".m-article__body", ".article-content",
        ".komunikat-content", "article", "main", "[class*='content']",
    ]

    content_el = None
    for sel in content_selectors:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 80:
            content_el = el
            break

    if not content_el:
        content_el = soup.find("body") or soup

    lines = []
    seen  = set()
    for el in content_el.find_all(["p", "h1", "h2", "h3", "h4", "li", "td"]):
        text = el.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 10 or text in seen:
            continue
        seen.add(text)
        tag = el.name
        if tag in ("h1", "h2"):
            lines.append(f"\n# {text}\n")
        elif tag in ("h3", "h4"):
            lines.append(f"\n## {text}\n")
        elif tag == "li":
            lines.append(f"- {text}")
        else:
            lines.append(text)

    result = "\n".join(lines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _safe_filename(announcement: dict, url: str, is_text: bool = False) -> str:
    d        = announcement["date"]
    date_str = d.strftime("%Y-%m-%d")
    # company jest już znormalizowana
    company  = _sanitize(announcement["company"])[:35]

    if is_text:
        title = announcement["title"]
        for sep in (": ", " — ", " - "):
            if sep in title:
                after_sep = title.split(sep, 1)[1].strip()
                if len(after_sep) > 10:
                    title = after_sep
                    break
        title_part = _sanitize(title)[:45]
    else:
        filename   = url.split("/")[-1].split("?")[0]
        filename   = re.sub(r"\.[^.]+$", "", filename)
        title_part = _sanitize(filename)[:45] or _sanitize(announcement["title"])[:35]

    return f"{date_str}_{company}_{title_part}"


def _sanitize(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', text)
    text = re.sub(r'_+', '_', text)
    return text.strip('_').strip()