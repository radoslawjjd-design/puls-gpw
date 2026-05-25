"""
Punkt wejścia — uruchamiany przez Cloud Run Job co 15 minut.
"""
import logging
import sys

# Structured JSON logging — Cloud Logging parsuje severity z pola "severity".
logging.basicConfig(
    stream  = sys.stdout,
    level   = logging.INFO,
    format  = '{"severity":"%(levelname)s","message":"%(message)s","logger":"%(name)s"}',
)

from pipeline import run


def main() -> None:
    try:
        run()
    except Exception as e:
        logging.critical(f"Pipeline crash nieobsłużony: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
