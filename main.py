import logging
import sys

from dotenv import load_dotenv

load_dotenv()

from db.bigquery import create_table_if_not_exists
from src.logging_setup import configure_logging
from src.notifier import send_alert

configure_logging()
logger = logging.getLogger(__name__)


def main():
    try:
        create_table_if_not_exists()
        logger.info("Pipeline started")
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
