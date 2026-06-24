"""One-off backfill of `companies` rows for announcements-only tickers (PUL-55).

Covers tickers with announcement history but no `companies` row — gaps the
listing-page seed (`scripts/seed_companies.py`) structurally can't reach
(delisted, suspended, or otherwise off today's live main-board page).

Run with:
    uv run python scripts/backfill_companies.py --dry-run   # log only, no writes
    uv run python scripts/backfill_companies.py              # writes to BigQuery
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import argparse
import logging

from db.bigquery import (
    create_companies_table_if_not_exists,
    ensure_companies_schema_current,
    list_tickers_missing_from_companies,
    upsert_company,
)
from src.company_profile import fetch_company_profile, profile_url_for_ticker
from src.exceptions import BigQueryError
from src.logging_setup import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill companies rows for announcements-only tickers.")
    parser.add_argument("--dry-run", action="store_true", default=False)
    args = parser.parse_args()

    configure_logging(level="INFO")
    logger = logging.getLogger(__name__)

    create_companies_table_if_not_exists()
    ensure_companies_schema_current()

    missing = list_tickers_missing_from_companies()
    logger.info("Found %d tickers missing from companies", len(missing))

    resolved_via_hop = 0
    minimal_fallback = 0
    for ticker, fallback_name in missing:
        profile = fetch_company_profile(profile_url_for_ticker(ticker))
        if profile is not None and profile.ticker == ticker:
            name, hop_url, isin = profile.company, profile.hop_url, profile.isin
            resolved_via_hop += 1
        else:
            name, hop_url, isin = fallback_name, None, None
            minimal_fallback += 1

        if args.dry_run:
            logger.info(
                "[dry-run] would upsert ticker=%s name=%s hop_url=%s isin=%s",
                ticker,
                name,
                hop_url,
                isin,
            )
        else:
            try:
                upsert_company(ticker, name, hop_url, isin)
            except BigQueryError as exc:
                logger.warning("upsert_company failed for ticker=%s: %s", ticker, exc)

    verb = "would resolve" if args.dry_run else "resolved"
    logger.info(
        "Done. missing=%d %s_via_hop=%d minimal_fallback=%d",
        len(missing),
        verb,
        resolved_via_hop,
        minimal_fallback,
    )


if __name__ == "__main__":
    main()
