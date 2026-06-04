import logging

from dotenv import load_dotenv

load_dotenv()

from db.bigquery import create_table_if_not_exists
from src.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger(__name__)


def main():
    create_table_if_not_exists()
    logger.info("Pipeline started")


if __name__ == "__main__":
    main()
