"""Polish number-format parsers shared by the scraper modules.

Polish market pages write `1 234,56` — comma decimal separator, non-breaking space
as the thousands separator — and use `-` for "no value". Both parsers return None
rather than 0 for missing data: a zero close would render as a real price.

Extracted from src/bankier_metrics.py (PUL-98) so that the official GPW/NewConnect
parser does not have to reach into the fallback source's internals.
"""


def parse_polish_float(text: str) -> float | None:
    """Parse a Polish-formatted number (comma decimal, space thousands) to float."""
    try:
        cleaned = (
            text.replace("\xa0", "")
                .replace("zł", "")
                .replace("%", "")
                .replace(" ", "")
                .strip()
        )
        return float(cleaned.replace(",", ".")) if cleaned else None
    except (ValueError, AttributeError):
        return None


def parse_int(text: str) -> int | None:
    """Parse a Polish-formatted integer (space thousands separator) to int."""
    try:
        cleaned = text.replace("\xa0", "").replace(" ", "").strip()
        return int(cleaned) if cleaned else None
    except (ValueError, AttributeError):
        return None
