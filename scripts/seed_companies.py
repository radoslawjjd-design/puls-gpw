"""Seed and reconcile the `companies` BigQuery table from bankier.pl listings.

Covers GPW (akcje) and NewConnect companies — including those with zero
ESPI/EBI history that the per-announcement parser hop (main.py) never reaches.

Run with:
    uv run python scripts/seed_companies.py --diff        # show listing vs BQ gap, no writes
    uv run python scripts/seed_companies.py --dry-run     # log only, no writes
    uv run python scripts/seed_companies.py               # upsert to BigQuery
    uv run python scripts/seed_companies.py --with-stats  # upsert + populate company_daily_stats
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import logging

from db.bigquery import (
    create_companies_table_if_not_exists,
    create_company_daily_stats_table_if_not_exists,
    ensure_companies_schema_current,
    ensure_company_daily_stats_schema_current,
    insert_company_if_absent,
    list_distinct_tickers,
    merge_company_daily_stats,
    upsert_company,
)
from src.bankier_metrics import fetch_listing_page
from src.company_profile import fetch_company_profile, profile_url_for_ticker
from src.exceptions import BigQueryError
from src.logging_setup import configure_logging

WARSAW = ZoneInfo("Europe/Warsaw")
_MARKETS = ["akcje", "new-connect"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed companies table from bankier.pl GPW + NewConnect listings."
    )
    parser.add_argument("--dry-run", action="store_true", help="Log only, no BQ writes.")
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Print listing vs BQ gap then exit (no writes).",
    )
    parser.add_argument(
        "--with-stats",
        action="store_true",
        help="Also merge today's trading snapshot into company_daily_stats.",
    )
    args = parser.parse_args()

    configure_logging(level="INFO")
    logger = logging.getLogger(__name__)

    # ── 1. Fetch combined listing from both markets ──────────────────────────
    listing: dict[str, dict] = {}
    for market in _MARKETS:
        data = fetch_listing_page(market)
        listing.update(data)
        logger.info("fetch_listing_page: market=%-12s symbols=%d", market, len(data))
    logger.info("Combined listing: %d unique symbols (GPW + NewConnect)", len(listing))

    # ── 2. --diff mode: compare listing vs BQ, then exit ────────────────────
    if args.diff:
        create_companies_table_if_not_exists()
        ensure_companies_schema_current()
        bq_tickers = set(list_distinct_tickers())
        listing_symbols = set(listing.keys())
        missing_from_bq = sorted(listing_symbols - bq_tickers)
        only_in_bq = sorted(bq_tickers - listing_symbols)
        print("\n-- Listing vs companies (BQ) ----------------------------------")
        print(f"  bankier.pl listing : {len(listing_symbols):>4d} symbols")
        print(f"  companies BQ       : {len(bq_tickers):>4d} tickers")
        print(f"  Missing from BQ    : {len(missing_from_bq):>4d}  <- run without --diff to fix")
        print(f"  Only in BQ (delisted?): {len(only_in_bq):>4d}")
        if missing_from_bq:
            print(f"\n  Tickers in listing but NOT in BQ ({len(missing_from_bq)}):")
            for t in missing_from_bq:
                print(f"    {t}")
        if only_in_bq:
            print(f"\n  Tickers in BQ but NOT in listing (delisted / suspended):")
            for t in only_in_bq:
                print(f"    {t}")
        print()
        return

    # ── 3. Ensure tables exist ───────────────────────────────────────────────
    if not args.dry_run:
        create_companies_table_if_not_exists()
        ensure_companies_schema_current()

    # ── 4. Upsert companies, optionally collect stats rows ───────────────────
    snapshot_date = datetime.now(WARSAW).date().isoformat()
    fetched_at = datetime.now(timezone.utc).isoformat()
    stats_rows: list[dict] = []
    upserted = 0
    fallback = 0
    failed = 0

    for symbol, trading_data in listing.items():
        hop_url = profile_url_for_ticker(symbol)
        profile = fetch_company_profile(hop_url)

        if profile is not None and profile.ticker:
            # Full profile available — use last-write-wins upsert.
            ticker, name, isin = profile.ticker, profile.company, profile.isin
            writer = upsert_company
        else:
            # Profile hop failed — insert minimal row only if the ticker is new.
            # Never overwrite an existing name with None.
            ticker, name, isin = symbol, None, None
            writer = insert_company_if_absent
            fallback += 1
            logger.debug("profile hop failed for symbol=%s — fallback to insert-if-absent", symbol)

        if args.dry_run:
            logger.info(
                "[dry-run] %-6s ticker=%-8s name=%s",
                writer.__name__,
                ticker,
                name or "(null)",
            )
        else:
            try:
                writer(ticker, name, hop_url, isin)
            except BigQueryError as exc:
                logger.warning("BQ write failed ticker=%s: %s", ticker, exc)
                failed += 1
                continue

        upserted += 1
        if args.with_stats:
            stats_rows.append(
                {
                    "ticker": ticker,
                    "snapshot_date": snapshot_date,
                    "fetched_at": fetched_at,
                    **{k: v for k, v in trading_data.items() if k != "company_name"},
                }
            )

    # ── 5. Optionally merge company_daily_stats ──────────────────────────────
    if args.with_stats and stats_rows and not args.dry_run:
        create_company_daily_stats_table_if_not_exists()
        ensure_company_daily_stats_schema_current()
        try:
            merge_company_daily_stats(stats_rows)
            logger.info(
                "company_daily_stats: merged %d rows for %s", len(stats_rows), snapshot_date
            )
        except BigQueryError as exc:
            logger.warning("merge_company_daily_stats failed: %s", exc)

    verb = "would write" if args.dry_run else "wrote"
    logger.info(
        "Done. listing=%d %s=%d  (profile_ok=%d fallback=%d) failed=%d",
        len(listing),
        verb,
        upserted,
        upserted - fallback,
        fallback,
        failed,
    )


if __name__ == "__main__":
    main()
