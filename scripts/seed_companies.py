"""One-off seed of the `companies` table from bankier.pl's full GPW listing.

Covers tickers with zero ESPI/EBI announcement history, which the regular
per-announcement parser hop (`main.py`) never reaches.

Run with:
    uv run python scripts/seed_companies.py --dry-run   # log only, no writes
    uv run python scripts/seed_companies.py              # writes to BigQuery
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import logging

from db.bigquery import create_companies_table_if_not_exists, ensure_companies_schema_current, upsert_company
from src.company_profile import extract_company_profile_links, fetch_company_profile
from src.exceptions import BigQueryError
from src.http_client import get
from src.logging_setup import configure_logging

_LISTING_URL = "https://www.bankier.pl/gielda/notowania/akcje"


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the companies table from bankier.pl's full GPW listing.")
    parser.add_argument("--dry-run", action="store_true", default=False)
    args = parser.parse_args()

    configure_logging(level="INFO")
    logger = logging.getLogger(__name__)

    create_companies_table_if_not_exists()
    ensure_companies_schema_current()

    resp = get(_LISTING_URL)
    links = extract_company_profile_links(resp.text)
    logger.info("Found %d company profile links on the listing page", len(links))

    upserted = 0
    failed = 0
    for link in links:
        profile = fetch_company_profile(link)
        if profile is None or profile.ticker is None:
            logger.warning("Failed to parse company profile: %s", link)
            failed += 1
            continue

        if args.dry_run:
            logger.info(
                "[dry-run] would upsert ticker=%s name=%s isin=%s hop_url=%s",
                profile.ticker,
                profile.company,
                profile.isin,
                profile.hop_url,
            )
        else:
            try:
                upsert_company(profile.ticker, profile.company, profile.hop_url, profile.isin)
            except BigQueryError as exc:
                logger.warning("upsert_company failed for ticker=%s: %s", profile.ticker, exc)
                failed += 1
                continue
        upserted += 1

    verb = "would upsert" if args.dry_run else "upserted"
    logger.info(
        "Done. links_found=%d %s=%d failed_to_parse=%d",
        len(links),
        verb,
        upserted,
        failed,
    )


if __name__ == "__main__":
    main()
