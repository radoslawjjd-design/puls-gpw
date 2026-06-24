import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.exceptions import ScraperError
from src.http_client import get

logger = logging.getLogger(__name__)


@dataclass
class CompanyProfile:
    ticker: str | None
    company: str | None
    isin: str | None
    hop_url: str


def fetch_company_profile(profile_url: str) -> CompanyProfile | None:
    """Fetch a bankier.pl profile/quote.html page and parse ticker/company/isin.

    Returns None on HTTP failure. Individual fields degrade to None when their
    markup is absent — never raises for a missing heading or isin attribute.
    """
    try:
        resp = get(profile_url)
    except ScraperError:
        logger.debug("fetch_company_profile: HTTP failed for %s", profile_url)
        return None

    soup = BeautifulSoup(resp.text, "html5lib")
    ticker, company = _extract_heading(soup)
    isin = _extract_isin(soup)
    return CompanyProfile(ticker=ticker, company=company, isin=isin, hop_url=profile_url)


def _extract_heading(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    heading = soup.select_one("span.a-heading__suffix.-blue.-with-dot")
    if not heading:
        return None, None
    raw = heading.get_text(strip=True)
    m = re.search(r"\(([^)]+)\)", raw)
    if not m:
        return None, None
    ticker = m.group(1).strip()
    company = raw[: m.start()].strip() or None
    return ticker, company


def _extract_isin(soup: BeautifulSoup) -> str | None:
    section = soup.select_one("#quotes-profile-header-box")
    if not section:
        return None
    return section.get("data-isin") or None


_BANKIER_BASE_URL = "https://www.bankier.pl"


def extract_company_profile_links(listing_html: str) -> list[str]:
    """Extract every company profile link from the bankier.pl listing page.

    De-duplicates while preserving order. Relative hrefs are resolved against
    `_BANKIER_BASE_URL`.
    """
    soup = BeautifulSoup(listing_html, "html5lib")
    links: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "profile/quote.html" not in href:
            continue
        resolved = urljoin(_BANKIER_BASE_URL, href)
        if resolved not in seen:
            seen.add(resolved)
            links.append(resolved)
    return links
