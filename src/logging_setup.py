"""JSON logging configuration for the puls-gpw pipeline (Cloud Run / Cloud Logging)."""
import logging
import sys

from pythonjsonlogger.json import JsonFormatter


def configure_logging(level: str = "INFO") -> None:
    """Configure root logger with JSON formatter compatible with Cloud Logging.

    Call once at pipeline startup, after load_dotenv(). Each module then
    declares its own logger: logger = logging.getLogger(__name__)

    Cloud Logging requires the field 'severity' (not 'levelname') to map
    log levels correctly; rename_fields handles this transparently.
    """
    numeric = logging.getLevelName(level.upper())
    if not isinstance(numeric, int):
        raise ValueError(f"Unknown log level: {level!r}")
    handler = logging.StreamHandler(sys.stderr)
    formatter = JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"levelname": "severity", "asctime": "timestamp"},
    )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
