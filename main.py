import logging
import sys

from dotenv import load_dotenv

load_dotenv()

from src.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

from db.bigquery import create_table_if_not_exists, insert_announcement
from src.notifier import send_alert
from src.scraper import scrape_new_announcements


def main():
    try:
        create_table_if_not_exists()
        new = scrape_new_announcements()
        if not new:
            logger.info("Pipeline completed: 0 new announcements")
            return
        for ann in new:
            insert_announcement(ann.bankier_url, ann.published_at, ann.title, None, None)
        logger.info("Pipeline completed: %d new announcements inserted", len(new))
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
