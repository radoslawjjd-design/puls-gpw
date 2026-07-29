import io

import pytest
from openpyxl import Workbook

from src.brokers.errors import BrokerParseError
from src.brokers.xlsx_reader import read_sheets

_HEADER = ["Type", "Ticker", "Instrument", "Time", "Amount", "ID", "Comment", "Product"]


def _workbook_bytes(*, preamble_rows: int = 4, header: list | None = None,
                    data: list[list] | None = None, broken_dimension: bool = False) -> bytes:
    """Build an XTB-shaped workbook in memory.

    The real exports must never be committed (they carry account numbers), so
    every fixture is synthesized to the same shape instead.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Cash Operations"
    for i in range(preamble_rows):
        ws.append([f"Meta {i}", "value"])
    ws.append(header if header is not None else _HEADER)
    for row in data or []:
        ws.append(row)
    if broken_dimension:
        # XTB declares "A1:A1" regardless of content. openpyxl's read-only mode
        # trusts that declaration and yields a single row unless it is reset.
        ws.calculate_dimension = lambda **kwargs: "A1:A1"
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_rows_are_keyed_by_header_never_by_position():
    data = [["Stock purchase", "TOA.PL", "Toya", None, -4128.24, "1", "OPEN BUY 412 @ 10.02", "My Trades"]]

    sheets = read_sheets(_workbook_bytes(data=data))

    assert sheets["Cash Operations"][0]["Ticker"] == "TOA.PL"
    assert sheets["Cash Operations"][0]["Amount"] == pytest.approx(-4128.24)


def test_header_is_found_below_the_metadata_preamble():
    # XTB puts account number and date range above the header; the header is not
    # at a fixed row, so it is located by its labels.
    data = [["Dividend", "KRU.PL", "Kruk", None, 722.0, "1", "", "My Trades"]]

    sheets = read_sheets(_workbook_bytes(preamble_rows=7, data=data))

    assert len(sheets["Cash Operations"]) == 1
    assert sheets["Cash Operations"][0]["Type"] == "Dividend"


def test_a_missing_expected_column_raises_naming_it():
    header = [c for c in _HEADER if c != "Amount"]

    with pytest.raises(BrokerParseError) as excinfo:
        read_sheets(_workbook_bytes(header=header))

    assert "Amount" in str(excinfo.value)


def test_all_rows_are_read_even_when_the_file_declares_a_1x1_dimension():
    # This is the real trap: XTB's export declares its dimension as a single
    # cell. Reading it in openpyxl's read-only mode without resetting that
    # declaration yields ONE row, so the import silently comes back empty
    # instead of failing loudly.
    data = [
        ["Dividend", "KRU.PL", "Kruk", None, float(n), str(n), "", "My Trades"]
        for n in range(1, 26)
    ]

    sheets = read_sheets(_workbook_bytes(data=data, broken_dimension=True))

    assert len(sheets["Cash Operations"]) == 25


def test_a_file_that_is_not_a_workbook_raises_a_parse_error():
    with pytest.raises(BrokerParseError):
        read_sheets(b"this is not a spreadsheet")
