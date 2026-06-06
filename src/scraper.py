import datetime
import logging
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from db.bigquery import announcement_id_for_url, get_processed_ids_since
from src.http_client import get

logger = logging.getLogger(__name__)

_WARSAW = ZoneInfo("Europe/Warsaw")


@dataclass
class Announcement:
    title: str
    espi_code: str
    bankier_url: str
    published_at: datetime.datetime
    source: str
    priority: str | None = None


def scrape_new_announcements() -> list[Announcement]:
    """Pobierz nowe (nie-duplikat) ogłoszenia z okna 15 min.

    Raises ScraperError jeśli HTTP fail po retry.
    Zwraca [] gdy brak nowych (normalne — INFO log, caller nie rzuca alertu).
    """
    window_minutes = int(os.environ.get("SCRAPE_WINDOW_MINUTES", "15"))
    max_pages = int(os.environ.get("MAX_PAGES_BANKIER", "5"))
    listing_url = os.environ.get(
        "BANKIER_LISTING_URL",
        "https://www.bankier.pl/gielda/wiadomosci/komunikaty-spolek/{page}",
    )

    now = datetime.datetime.now(_WARSAW)
    cutoff = now - datetime.timedelta(minutes=window_minutes)
    dedup_cutoff = now - datetime.timedelta(minutes=window_minutes * 2)

    known_ids = get_processed_ids_since(dedup_cutoff)

    new_announcements: list[Announcement] = []
    seen = 0
    pages_fetched = 0

    for page in range(1, max_pages + 1):
        url = listing_url.format(page=page)
        resp = get(url)
        pages_fetched += 1

        soup = BeautifulSoup(resp.text, "html5lib")
        items = soup.select(".m-quotes-announcements-item")

        if not items:
            logger.warning("Bankier page %d: no items found", page)
            break

        page_min_dt: datetime.datetime | None = None

        for item in items:
            date_el = item.select_one(".m-quotes-announcements-item__date")
            if not date_el:
                continue

            raw = date_el.get_text(strip=True)
            try:
                naive_dt = datetime.datetime.strptime(raw, "%d.%m.%Y %H:%M")
            except ValueError:
                continue

            item_dt = naive_dt.replace(tzinfo=_WARSAW)  # replace() safe outside DST fold; ambiguity accepted for 15-min window

            if page_min_dt is None or item_dt < page_min_dt:
                page_min_dt = item_dt

            if item_dt < cutoff:
                continue

            anchor = item.select_one(".m-quotes-announcements-item__anchor")
            if not anchor:
                continue

            title = anchor.get_text(strip=True)
            href = anchor.get("href", "")
            if isinstance(href, list):
                href = href[0]
            if not href.startswith("http"):
                href = "https://www.bankier.pl" + href

            espi_code = title.split(":")[0].strip() if ":" in title else ""

            source_el = item.select_one(".a-quotes-badge .value")
            source = source_el.get_text(strip=True).lower() if source_el else "espi"

            priority_el = item.select_one(".a-quotes-badge.-priority")
            priority = priority_el.get_text(strip=True) if priority_el else None

            ann_id = announcement_id_for_url(href)
            seen += 1
            if ann_id in known_ids:
                continue

            new_announcements.append(
                Announcement(
                    title=title,
                    espi_code=espi_code,
                    bankier_url=href,
                    published_at=item_dt,
                    source=source,
                    priority=priority,
                )
            )
            known_ids.add(ann_id)

        if page_min_dt is None:
            logger.warning("Bankier page %d: no parseable dates — stopping pagination", page)
            break
        if page_min_dt < cutoff:
            break

    logger.info(
        "Scraper: %d new / %d seen / %d pages",
        len(new_announcements), seen, pages_fetched,
    )
    return new_announcements
