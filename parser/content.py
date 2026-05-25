"""
Parser treści ogłoszeń ESPI/EBI.

Główna funkcja: extract_content(ann: dict) -> str

Uproszczona wersja względem oryginału: zamiast zwracać listę plików
do dalszego przetwarzania, od razu zwraca połączony tekst gotowy dla Gemini.

Pipeline w ramach funkcji:
  1. discover() — wchodzi na stronę ogłoszenia, identyfikuje zasoby do pobrania
  2. fetch + extract — pobiera i konwertuje na tekst (PDF/XHTML/HTML)
  3. Zwraca połączony tekst, max MAX_CONTENT_CHARS

Fast-path dla raportów finansowych KNF: zamiast pobierać 10-20 załączników
(audyt, XBRL, opinie RN), używa table.seauid2 z Bankier — zawiera WYBRANE
DANE FINANSOWE wystarczające do sentiment analizy.
"""
import logging
import re
from io import BytesIO
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

MAX_CONTENT_CHARS = 12_000  # soft limit przed wysłaniem do Gemini

_FINANCIAL_TITLE_PATTERNS = [
    re.compile(r"\bwyniki\s+finansowe\b", re.IGNORECASE),
    re.compile(r"\braport\s+(okresowy|roczny|polroczny|kwartalny)\b", re.IGNORECASE),
    re.compile(r"\b(RR|SRR|PSr|QSr)\b", re.IGNORECASE),
]
_SEAUID_MIN_TEXT_LEN        = 800
_SEAUID_FINANCIAL_KEYWORDS  = ["przychody", "ebit", "zysk", "aktywa", "kapital", "kapitał"]


def extract_content(ann: dict) -> str:
    """
    Zwraca treść ogłoszenia jako jeden string (max ~12 000 znaków).
    Zwraca pusty string gdy nie uda się pobrać żadnej treści.
    """
    url   = ann.get("url") or ann.get("bankier_url", "")
    title = ann.get("title", "")

    if not url:
        logger.warning(f"Brak URL w ogłoszeniu: {title!r}")
        return ""

    resp = get(url)
    if resp is None:
        logger.warning(f"Nie można pobrać ogłoszenia: {url}")
        return ""

    soup      = BeautifulSoup(resp.text, "html5lib")
    final_url = resp.url

    # Fast-path: raporty finansowe KNF — table.seauid2 zamiast N załączników
    if _is_financial_title(title):
        seauid_text = _extract_seauid(soup)
        if seauid_text:
            logger.info(f"Fast-path KNF seauid2: {len(seauid_text)} znaków ({title[:60]})")
            return seauid_text[:MAX_CONTENT_CHARS]
        logger.warning(f"Fast-path KNF miss dla: {title[:60]!r} — fallback do załączników")

    # Przekierowanie na ESPI/PAP jeśli ogłoszenie jest na Bankier
    if "bankier.pl" in final_url:
        espi_url = _find_espi_redirect(soup, final_url)
        if espi_url and espi_url != final_url:
            logger.info(f"Przekierowanie ESPI: {espi_url}")
            resp2 = get(espi_url)
            if resp2:
                soup      = BeautifulSoup(resp2.text, "html5lib")
                final_url = espi_url

    # Szukaj załączników PDF/XHTML
    pdf_links   = _find_attachments(soup, final_url, PDF_EXTENSIONS)
    xhtml_links = _find_attachments(soup, final_url, XHTML_EXTENSIONS)

    texts: list[str] = []

    for pdf_url in pdf_links[:3]:  # max 3 PDF-y żeby nie timeoutować
        text = _extract_pdf_url(pdf_url)
        if text:
            texts.append(text)

    for xhtml_url in xhtml_links[:2]:
        text = _extract_xhtml_url(xhtml_url)
        if text:
            texts.append(text)

    # Fallback: tekst z HTML strony ogłoszenia
    if not texts:
        text = _extract_html(soup, final_url)
        if text:
            texts.append(text)

    combined = "\n\n---\n\n".join(texts)
    return combined[:MAX_CONTENT_CHARS]


# ── Ekstraktory ───────────────────────────────────────────────────────────────

def _extract_pdf_url(url: str) -> str:
    data = download_binary(url)
    if not data or len(data) < 1000:
        logger.warning(f"PDF zbyt mały lub błąd pobierania: {url}")
        return ""
    return _extract_pdf_bytes(data)


def _extract_pdf_bytes(data: bytes) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(data))
        pages  = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                pages.append(text)
        return "\n\n".join(pages)
    except Exception as e:
        logger.warning(f"Błąd ekstrakcji PDF: {e}")
        return ""


def _extract_xhtml_url(url: str) -> str:
    data = download_binary(url)
    if not data or len(data) < 500:
        return ""
    try:
        soup = BeautifulSoup(data, "html5lib")
        return _extract_html(soup, url)
    except Exception as e:
        logger.warning(f"Błąd ekstrakcji XHTML {url}: {e}")
        return ""


def _extract_html(soup: BeautifulSoup, url: str) -> str:
    for tag in soup.select(
        "nav, header, footer, script, style, noscript, "
        ".ad, .menu, .sidebar, [class*='banner'], "
        "[class*='cookie'], [class*='footer'], [class*='header']"
    ):
        tag.decompose()

    content_el = None
    for sel in [
        "#emitent", ".m-article__body", ".article-content",
        ".komunikat-content", "article", "main", "[class*='content']",
    ]:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 80:
            content_el = el
            break
    if not content_el:
        content_el = soup.find("body") or soup

    lines: list[str] = []
    seen:  set[str]  = set()
    for el in content_el.find_all(["p", "h1", "h2", "h3", "h4", "li", "td"]):
        text = re.sub(r"\s+", " ", el.get_text(separator=" ", strip=True)).strip()
        if len(text) < 10 or text in seen:
            continue
        seen.add(text)
        if el.name in ("h1", "h2"):
            lines.append(f"\n# {text}\n")
        elif el.name in ("h3", "h4"):
            lines.append(f"\n## {text}\n")
        elif el.name == "li":
            lines.append(f"- {text}")
        else:
            lines.append(text)

    result = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return result.strip()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_financial_title(title: str) -> bool:
    return any(p.search(title) for p in _FINANCIAL_TITLE_PATTERNS)


def _extract_seauid(soup: BeautifulSoup) -> str | None:
    tables = soup.find_all(
        "table",
        class_=lambda c: c and "seauid" in (c if isinstance(c, str) else " ".join(c)),
    )
    for table in tables:
        text = table.get_text(" | ", strip=True)
        if len(text) < _SEAUID_MIN_TEXT_LEN:
            continue
        if not any(kw in text.lower() for kw in _SEAUID_FINANCIAL_KEYWORDS):
            continue
        return f"WYBRANE DANE FINANSOWE (bankier table.seauid2):\n\n{text}"
    return None


def _find_espi_redirect(soup: BeautifulSoup, base_url: str) -> str | None:
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(d in href for d in ["espi.com.pl", "pap.pl/node", "knf.gov.pl"]):
            return href if href.startswith("http") else urljoin(base_url, href)
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
