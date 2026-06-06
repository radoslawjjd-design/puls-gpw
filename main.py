import logging
import sys

from dotenv import load_dotenv

load_dotenv()

from src.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

from db.bigquery import (
    BigQueryError,
    create_table_if_not_exists,
    ensure_schema_current,
    insert_announcement,
    update_parsed_content,
)
from src.notifier import send_alert
from src.parser import parse_announcement
from src.scraper import scrape_new_announcements


def main():
    try:
        create_table_if_not_exists()
        ensure_schema_current()
        new = scrape_new_announcements()
        if not new:
            logger.info("Pipeline completed: 0 new announcements")
            return
        for ann in new:
            try:
                ann_id = insert_announcement(ann.bankier_url, ann.published_at, ann.title, None, None, ann.priority)
                parsed = parse_announcement(ann, ann_id)
                update_parsed_content(ann_id, parsed.parsed_content, parsed.ticker, parsed.company)
            except BigQueryError:
                raise  # propagate to outer except → send_alert
            except Exception:
                logger.exception("Unexpected error processing %s — skipping", ann.bankier_url)
        logger.info("Pipeline completed: %d announcements scraped and parsed", len(new))
    except Exception as exc:
        logger.exception("Pipeline failed")
        try:
            send_alert(exc)
            logger.info("Alert email sent")
        except Exception as alert_exc:
            logger.error("Failed to send alert: %s", alert_exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
