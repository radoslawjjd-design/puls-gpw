import io
import logging
import os
import re
from dataclasses import dataclass

from urllib.parse import urljoin

import pypdf
from bs4 import BeautifulSoup

from src.exceptions import ScraperError
from src.http_client import download_binary, get
from src.scraper import Announcement

# Parse failures are non-fatal: all errors return None fields rather than raising ParserError.
# ParserError (src.exceptions) is reserved for future callers that need to distinguish failure modes.
logger = logging.getLogger(__name__)

_MAX_PDFS = int(os.environ.get("PARSER_MAX_PDFS", "3"))
_MAX_CHARS = int(os.environ.get("PARSED_CONTENT_MAX_CHARS", "15000"))
_TABLE_SCORE_MIN = int(os.environ.get("PARSER_TABLE_SCORE_MIN", "30"))

_BLOCKED_FILENAME_KEYWORDS = [
    "regulamin", "polityka_prywatnosci", "polityka_plikow",
    "cookies", "privacy_policy", "terms_of_service",
]


@dataclass
class ParsedContent:
    announcement_id: str
    parsed_content: str | None
    ticker: str | None
    company: str | None


def parse_announcement(ann: Announcement, announcement_id: str) -> ParsedContent:
    """Fetch announcement page and extract content + ticker/company.

    Never raises — all failures are logged as WARNING and return None fields.
    """
    try:
        resp = get(ann.bankier_url)
    except ScraperError:
        logger.warning("parse_announcement: HTTP failed for %s", ann.bankier_url)
        return ParsedContent(announcement_id, None, None, None)

    soup = BeautifulSoup(resp.text, "html5lib")
    ticker, company = _extract_ticker_company(soup, ann.bankier_url)

    seauid2_text = _extract_seauid2(soup)
    pdf_links = _find_pdf_links(soup, ann.bankier_url)

    if seauid2_text:
        if pdf_links:
            # Combine: seauid2 provides the KNF header/summary, PDFs provide attachments
            # (e.g. Form A with exact shareholding data).  Budget remaining chars for PDFs.
            remaining = _MAX_CHARS - len(seauid2_text)
            pdf_text = ""
            if remaining > 0:
                for url in pdf_links:
                    data = download_binary(url)
                    if data:
                        pdf_text += _extract_pdf_text(data)
                    if len(pdf_text) >= remaining:
                        break
            if pdf_text.strip():
                combined = (seauid2_text + "\n\n" + pdf_text).strip()
                logger.info("Parser: seauid2+pdf for %s", ann.bankier_url)
                return ParsedContent(announcement_id, combined[:_MAX_CHARS], ticker, company)
        logger.info("Parser: seauid2 for %s", ann.bankier_url)
        return ParsedContent(announcement_id, seauid2_text[:_MAX_CHARS], ticker, company)

    if pdf_links:
        all_text = ""
        for url in pdf_links:
            data = download_binary(url)
            if data:
                all_text += _extract_pdf_text(data)
            if len(all_text) >= _MAX_CHARS:
                break
        if all_text.strip():
            logger.info("Parser: pdf for %s", ann.bankier_url)
            return ParsedContent(announcement_id, all_text[:_MAX_CHARS], ticker, company)

    text = _extract_html_fallback(soup)
    if text:
        logger.info("Parser: html for %s", ann.bankier_url)
        return ParsedContent(announcement_id, text[:_MAX_CHARS], ticker, company)

    logger.warning("Parser: none for %s", ann.bankier_url)
    return ParsedContent(announcement_id, None, ticker, company)


def _extract_seauid2(soup: BeautifulSoup) -> str | None:
    table = soup.find(
        "table",
        class_=lambda c: c and "seauid2" in (c if isinstance(c, str) else " ".join(c)),
    )
    if not table:
        return None
    text = table.get_text(" | ", strip=True)
    return text if len(text) >= 100 else None


def _find_pdf_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        lower_path = href.lower().split("?")[0]
        if not lower_path.endswith(".pdf"):
            continue
        filename = lower_path.split("/")[-1]
        if any(kw in filename for kw in _BLOCKED_FILENAME_KEYWORDS):
            continue
        if not href.startswith("http"):
            href = urljoin(base_url, href)
        if href not in found:
            found.append(href)
        if len(found) >= _MAX_PDFS:
            break
    return found


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        page_texts = [page.extract_text() or "" for page in reader.pages]

        # For short PDFs read sequentially — no scoring overhead needed.
        if len(page_texts) <= 8:
            return "".join(page_texts)[:_MAX_CHARS]

        # For longer PDFs prioritise pages that are likely financial tables by
        # counting numeric tokens per page.  Always include the first two pages
        # as document context, then append table-heavy pages in document order.
        def _num_score(text: str) -> int:
            return len(re.findall(r"\b\d+[.,]?\d*\b", text))

        scores = [_num_score(t) for t in page_texts]
        context_indices = list(range(2))
        table_indices = [i for i, s in enumerate(scores) if s >= _TABLE_SCORE_MIN]
        selected = sorted(set(context_indices + table_indices))

        result = ""
        for i in selected:
            result += page_texts[i]
            if len(result) >= _MAX_CHARS:
                break
        return result[:_MAX_CHARS]
    except Exception as exc:
        logger.warning("_extract_pdf_text: failed — %s", exc)
        return ""


def _extract_html_fallback(soup: BeautifulSoup) -> str | None:
    section = soup.select_one("section.o-article-content")
    if not section:
        return None
    for br in section.find_all("br"):
        br.replace_with("§BR§")
    segments = [s.strip() for s in section.get_text().split("§BR§") if s.strip()]
    # segments[0] is the Bankier AI summary preamble; segments[1] is the announcement body.
    # Fall back to segments[0] if there is no preamble (layout change or bare content).
    if len(segments) >= 2:
        text = segments[1]
    elif segments:
        text = segments[0]
    else:
        text = None
    return text if text and len(text) >= 50 else None


def _extract_ticker_company(soup: BeautifulSoup, base_url: str) -> tuple[str | None, str | None]:
    link = soup.find("a", href=lambda h: h and "profile/quote.html" in h)
    if not link:
        return None, None
    profile_url = link["href"]
    if not profile_url.startswith("http"):
        profile_url = urljoin(base_url, profile_url)
    try:
        profile_resp = get(profile_url)
    except ScraperError:
        logger.debug("_extract_ticker_company: HTTP failed for %s", profile_url)
        return None, None
    profile_soup = BeautifulSoup(profile_resp.text, "html5lib")
    heading = profile_soup.select_one("span.a-heading__suffix.-blue.-with-dot")
    if not heading:
        return None, None
    raw = heading.get_text(strip=True)
    m = re.search(r"\(([^)]+)\)", raw)
    if not m:
        return None, None
    ticker = m.group(1).strip()
    company = raw[: m.start()].strip() or None
    return ticker, company
