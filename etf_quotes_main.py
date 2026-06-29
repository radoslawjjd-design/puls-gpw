"""Cloud Run Job entrypoint for the daily ETF/ETC/ETN quotes ingestion pipeline."""
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
    create_etf_instruments_table_if_not_exists,
    create_etf_quotes_table_if_not_exists,
    ensure_etf_instruments_schema_current,
    ensure_etf_quotes_schema_current,
    merge_etf_instruments,
    merge_etf_quotes,
)
from src.gpw_etf_metrics import fetch_etf_page
from src.notifier import send_alert

WARSAW = ZoneInfo("Europe/Warsaw")


def main() -> None:
    try:
        create_etf_instruments_table_if_not_exists()
        ensure_etf_instruments_schema_current()
        create_etf_quotes_table_if_not_exists()
        ensure_etf_quotes_schema_current()

        snapshot_date = datetime.now(WARSAW).date()
        fetched_at = datetime.now(timezone.utc)

        instruments, quotes = fetch_etf_page(snapshot_date, fetched_at)

        if not instruments:
            raise RuntimeError(
                f"no instruments parsed for {snapshot_date} — aborting to preserve existing data"
            )

        merge_etf_instruments(list(instruments.values()))
        merge_etf_quotes(quotes)

        logger.info(
            "etf_quotes_main: done — instruments=%d quotes=%d date=%s",
            len(instruments), len(quotes), snapshot_date,
        )

    except Exception as exc:
        logger.exception("etf_quotes_main: pipeline failed")
        try:
            send_alert(exc)
            logger.info("etf_quotes_main: alert email sent")
        except Exception as alert_exc:
            logger.error("etf_quotes_main: failed to send alert: %s", alert_exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
