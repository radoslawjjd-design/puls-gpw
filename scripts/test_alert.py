"""End-to-end test for JSON logging and email alert mechanisms.

Run with:
    uv run python scripts/test_alert.py --dry-run   # skip actual email
    uv run python scripts/test_alert.py             # sends real email (requires .env)
"""
import logging
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.logging_setup import configure_logging
from src.notifier import send_alert


def main() -> None:
    configure_logging(level="DEBUG")
    logger = logging.getLogger(__name__)

    # Step 1 — one log per level; JSON output on stderr lets you confirm fields visually
    logger.debug("test-alert [debug]")
    logger.info("test-alert [info]")
    logger.warning("test-alert [warning]")
    logger.error("test-alert [error]")

    # Step 2 — raise, catch, log traceback
    try:
        raise ValueError("test-alert-F03")
    except ValueError as exc:
        tb_lines = len(traceback.format_exc().splitlines())
        logger.exception("Caught test exception (traceback lines: %d)", tb_lines)

        # Step 3/4 — real send or dry-run
        if "--dry-run" in sys.argv:
            owner = os.environ.get("OWNER_EMAIL", "<not set>")
            print(f"[dry-run] would send alert email to {owner}")
        else:
            send_alert(exc)
            print("Alert email sent.")

    print("\nAll steps passed.")


if __name__ == "__main__":
    main()
