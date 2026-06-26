"""Cloud Run Job entrypoint for the daily company-stats snapshot ingestion pipeline."""
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from src.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

from db.bigquery import (
    BigQueryError,
    create_company_daily_stats_table_if_not_exists,
    ensure_company_daily_stats_schema_current,
    insert_company_daily_stats,
    list_companies_with_hop_info,
)
from src.bankier_metrics import fetch_daily_stats, symbol_from_hop_url
from src.notifier import send_alert

WARSAW = ZoneInfo("Europe/Warsaw")


def main() -> None:
    try:
        create_company_daily_stats_table_if_not_exists()
        ensure_company_daily_stats_schema_current()
        companies = list_companies_with_hop_info()

        snapshot_date = datetime.now(WARSAW).date()
        processed = 0
        skipped = 0

        for company in companies:
            ticker = company["ticker"]
            hop_url = company.get("hop_url")
            isin = company.get("isin")

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

            stats = fetch_daily_stats(isin, symbol)
            if stats is None:
                # fetch_daily_stats already logs the failure at WARNING level
                skipped += 1
                continue

            try:
                insert_company_daily_stats(
                    ticker=ticker,
                    snapshot_date=snapshot_date,
                    kurs_odniesienia=stats.get("kurs_odniesienia"),
                    kurs_otwarcia=stats.get("kurs_otwarcia"),
                    kurs_min=stats.get("kurs_min"),
                    kurs_max=stats.get("kurs_max"),
                    wolumen_obrotu=stats.get("wolumen_obrotu"),
                    wartosc_obrotu=stats.get("wartosc_obrotu"),
                    liczba_transakcji=stats.get("liczba_transakcji"),
                    stopa_zwrotu_1r=stats.get("stopa_zwrotu_1r"),
                    kapitalizacja=stats.get("kapitalizacja"),
                    rynek=stats.get("rynek"),
                    system=stats.get("system"),
                )
                processed += 1
            except BigQueryError:
                logger.warning(
                    "company_stats_main: BQ insert failed for ticker=%s — skipping", ticker
                )
                skipped += 1

        logger.info(
            "company_stats_main: done — processed=%d skipped=%d total=%d",
            processed, skipped, len(companies),
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
