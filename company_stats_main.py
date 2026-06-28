"""Cloud Run Job entrypoint for the daily company-stats snapshot ingestion pipeline."""
import logging
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from src.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

from db.bigquery import (
    create_company_daily_stats_table_if_not_exists,
    ensure_company_daily_stats_schema_current,
    list_companies_with_hop_info,
    merge_company_daily_stats,
)
from src.bankier_metrics import fetch_listing_page, symbol_from_hop_url
from src.notifier import send_alert

WARSAW = ZoneInfo("Europe/Warsaw")


def main() -> None:
    try:
        create_company_daily_stats_table_if_not_exists()
        ensure_company_daily_stats_schema_current()
        companies = list_companies_with_hop_info()

        gpw = fetch_listing_page("akcje")
        nc = fetch_listing_page("new-connect")
        listing = {**gpw, **nc}
        logger.info(
            "company_stats_main: listing loaded — %d symbols (GPW=%d NC=%d)",
            len(listing), len(gpw), len(nc),
        )

        snapshot_date = datetime.now(WARSAW).date()
        fetched_at = datetime.now(timezone.utc).isoformat()
        rows = []
        skipped = 0

        for company in companies:
            ticker = company["ticker"]
            hop_url = company.get("hop_url")

            if not hop_url:
                logger.warning(
                    "company_stats_main: no hop_url for ticker=%s — skipping", ticker
                )
                skipped += 1
                continue

            symbol = symbol_from_hop_url(hop_url)
            if symbol is None:
                logger.warning(
                    "company_stats_main: no symbol in hop_url for ticker=%s — skipping", ticker
                )
                skipped += 1
                continue

            stats = listing.get(symbol)
            if stats is None:
                logger.warning(
                    "company_stats_main: no listing data for ticker=%s symbol=%s — skipping",
                    ticker, symbol,
                )
                skipped += 1
                continue

            rows.append({
                "ticker": ticker,
                "snapshot_date": snapshot_date.isoformat(),
                "fetched_at": fetched_at,
                **{k: v for k, v in stats.items() if k != "company_name"},
            })

        if not rows:
            raise RuntimeError(
                f"no rows built for {snapshot_date} — aborting to preserve existing data"
            )

        merge_company_daily_stats(rows)

        logger.info(
            "company_stats_main: done — processed=%d skipped=%d total=%d",
            len(rows), skipped, len(companies),
        )

    except Exception as exc:
        logger.exception("company_stats_main: pipeline failed")
        try:
            send_alert(exc)
            logger.info("company_stats_main: alert email sent")
        except Exception as alert_exc:
            logger.error("company_stats_main: failed to send alert: %s", alert_exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
