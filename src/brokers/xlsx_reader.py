"""The only place in the broker package that touches spreadsheet bytes.

Isolated so the parsers can be tested against plain dicts. Mirrors how
``scripts/backfill_historical_closes.py`` keeps its pure logic away from I/O.
"""

import io

from openpyxl import load_workbook

from src.brokers.errors import BrokerParseError

# Column labels a sheet must carry. Rows are keyed by header, never by position:
# positional indexing is what let a defect live for a month in PUL-98.
_REQUIRED_COLUMNS = {
    "Cash Operations": ("Type", "Ticker", "Instrument", "Time", "Amount", "ID", "Comment"),
}

# How far down to look for the header. XTB puts an account-number/date-range
# preamble above it, so the header row is located by its labels rather than
# assumed to sit at a fixed index.
_MAX_HEADER_SCAN = 25


def read_sheets(data: bytes) -> dict[str, list[dict]]:
    """Read a workbook into ``{sheet name: [row dict, ...]}``.

    Rows are keyed by column header. A sheet missing an expected column raises
    ``BrokerParseError`` naming that column rather than guessing.
    """
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except BrokerParseError:
        raise
    except Exception as exc:  # openpyxl raises a wide range for malformed input
        raise BrokerParseError(f"nie udalo sie odczytac pliku xlsx: {exc}") from exc

    try:
        return {sheet.title: _read_one(sheet) for sheet in workbook.worksheets}
    finally:
        workbook.close()


def _read_one(sheet) -> list[dict]:
    # XTB declares the sheet dimension as a single cell no matter how much data
    # it holds. In read-only mode openpyxl trusts that declaration, so without
    # this reset the import comes back with one row instead of hundreds —
    # silently empty rather than loudly broken.
    if hasattr(sheet, "reset_dimensions"):
        sheet.reset_dimensions()

    rows = list(sheet.iter_rows(values_only=True))
    required = _REQUIRED_COLUMNS.get(sheet.title)
    header_index = _find_header(rows, required)
    if header_index is None:
        if required is None:
            return []
        missing = _missing_columns(rows, required)
        raise BrokerParseError(
            f"arkusz '{sheet.title}' nie ma oczekiwanych kolumn: {', '.join(missing)}"
        )

    header = [str(cell).strip() if cell is not None else "" for cell in rows[header_index]]
    out: list[dict] = []
    for row in rows[header_index + 1:]:
        if row is None or all(cell is None or cell == "" for cell in row):
            continue
        out.append({name: value for name, value in zip(header, row) if name})
    return out


def _find_header(rows: list[tuple], required: tuple[str, ...] | None) -> int | None:
    if required is None:
        return None
    wanted = set(required)
    for index, row in enumerate(rows[:_MAX_HEADER_SCAN]):
        labels = {str(cell).strip() for cell in row if cell is not None}
        if wanted <= labels:
            return index
    return None


def _missing_columns(rows: list[tuple], required: tuple[str, ...]) -> list[str]:
    """Best-effort report of which expected columns were absent."""
    best: list[str] = list(required)
    for row in rows[:_MAX_HEADER_SCAN]:
        labels = {str(cell).strip() for cell in row if cell is not None}
        missing = [name for name in required if name not in labels]
        if len(missing) < len(best):
            best = missing
    return best
