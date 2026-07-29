"""E2E tests — broker-export import (PUL-95 Phase 4)."""
import copy
import io
import re
from datetime import datetime

import pytest
from playwright.sync_api import Page, expect

from tests.e2e import conftest as e2e_conftest
from tests.e2e.conftest import e2e_login_email


@pytest.fixture(autouse=True)
def _restore_positions_store():
    """Undo this file's writes to the session-wide fake wallet.

    The store is module-level and the server fixture is session-scoped, so a
    commit here would otherwise delete PKO for every test that runs afterwards.
    Scoped to this file on purpose: PUL-90 showed that adding entities to the
    shared conftest destabilizes the whole suite through strict-mode locators.
    """
    snapshot = copy.deepcopy(e2e_conftest._portfolio_positions_store)
    yield
    e2e_conftest._portfolio_positions_store.clear()
    e2e_conftest._portfolio_positions_store.update(snapshot)


_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_HEADER = ["Type", "Ticker", "Instrument", "Time", "Amount", "ID", "Comment", "Product"]
_T0 = datetime(2026, 7, 1, 10, 0, 0)

# Shaped so every disclosure section has something to render: XTB and ZZZ stay
# open (ZZZ is unknown to the app), PKO is bought and fully sold (and IS held, so
# it lands in `closed`), and CDR is held but absent from the file (`untouched`).
# A section no fixture ever populates is a section no test ever proves — that is
# how PUL-100's `excluded` branch shipped unexercised.
_ROWS = [
    ["Stock purchase", "XTB.PL", "XTB SA", _T0, -500.0, "1", "OPEN BUY 20 @ 25.00", "My Trades"],
    ["Stock purchase", "ZZZ.PL", "Spolka Zzz", _T0, -200.0, "2", "OPEN BUY 20 @ 10.00", "My Trades"],
    ["Stock purchase", "PKO.PL", "PKO BP", _T0, -400.0, "3", "OPEN BUY 10 @ 40.00", "My Trades"],
    ["Stock sell", "PKO.PL", "PKO BP", datetime(2026, 7, 2, 10, 0, 0), 450.0, "4",
     "CLOSE BUY 10 @ 45.00", "My Trades"],
    ["Dividend", "XTB.PL", "XTB SA", _T0, 60.0, "5", "", "My Trades"],
]


def _export_bytes() -> bytes:
    """Synthesize an XTB-shaped export; the real files carry account numbers."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Cash Operations"
    for i in range(4):
        ws.append([f"Meta {i}", "value"])
    ws.append(_HEADER)
    for row in _ROWS:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _login_and_open(page: Page, base_url: str) -> None:
    e2e_login_email(page, base_url)
    page.get_by_role("button", name="Mój portfel").click()
    # PUL-90: the default "Wszystkie" tab is read-only — import needs a real wallet.
    page.locator("#pp-portfolio-tabs .pp-portfolio-tab", has_text="Główny").click()


def _attach_export(page: Page) -> None:
    page.locator("#pp-import-file").set_input_files({
        "name": "export.xlsx", "mimeType": _XLSX_MIME, "buffer": _export_bytes(),
    })


def test_write_controls_never_flash_visible_before_the_wallet_list_arrives(
    page: Page, live_server_url: str
):
    """Risk: entry lands on the read-only "Wszystkie" tab.

    Rendered visible, these controls appear on first paint and vanish once the
    wallet list resolves — which reads as the feature being broken rather than as
    the tab being read-only.

    The wallet response is held open on purpose. Asserting after it lands proves
    nothing: `_ppSyncAddBtnVisibility` has hidden them by then, so the assertion
    passes with or without the fix. The transient state IS the bug, so the test
    has to look while the window is open.
    """
    held = {"done": False}

    def _hold(route):
        # First request only. A handler that sleeps on every match is still armed
        # at teardown, and sleeping on a closing page poisons the NEXT test's setup.
        if not held["done"]:
            held["done"] = True
            page.wait_for_timeout(1500)
        route.continue_()

    try:
        page.route("**/api/portfolio/wallets*", _hold)
        e2e_login_email(page, live_server_url)
        page.get_by_role("button", name="Mój portfel").click()
        page.wait_for_timeout(200)

        # `is_visible()` on purpose, never `expect(...).to_be_hidden()`: the latter
        # retries for seconds and so passes the moment the flash ends, which makes
        # it structurally unable to catch a transient state. Verified — with the
        # style attribute removed this assertion fails, the expect() form does not.
        assert page.locator("#pp-table-wrap").is_visible(), "widok się nie wyrenderował"
        assert not page.locator("#pp-import-btn").is_visible()
        assert not page.locator("#pp-add-toggle-btn").is_visible()
        # Export stays available throughout — reading an aggregate wallet is allowed.
        assert page.locator("#pp-export-csv-btn").is_visible()
    finally:
        page.unroute_all(behavior="ignoreErrors")


def test_import_button_is_hidden_in_read_only_all_mode(page: Page, live_server_url: str):
    """Risk: importing into the "Wszystkie" aggregate has no write path — the button must not appear."""
    _login_and_open(page, live_server_url)

    expect(page.locator("#pp-import-btn")).to_be_visible()

    page.locator("#pp-portfolio-tabs .pp-portfolio-tab", has_text="Wszystkie").click()

    expect(page.locator("#pp-import-btn")).to_be_hidden()


def test_import_preview_renders_every_disclosure_section(page: Page, live_server_url: str):
    """Risk: a consequence the preview omits is one the user cannot refuse."""
    _login_and_open(page, live_server_url)

    page.locator("#pp-import-btn").click()
    expect(page.locator("#pp-import-overlay")).to_be_visible()
    _attach_export(page)

    with page.expect_response(re.compile(r"/api/portfolio/import/preview")):
        page.locator("#pp-import-preview-btn").click()

    expect(page.locator("#pp-import-result")).to_be_visible()
    # To be written, including the one nothing can price.
    expect(page.locator("#pp-import-positions")).to_contain_text("XTB")
    expect(page.locator("#pp-import-unknown")).to_contain_text("ZZZ")
    # Closed in the file AND held today — the only rows a commit deletes.
    expect(page.locator("#pp-import-closed")).to_contain_text("PKO")
    # Held, absent from the file, left alone. This is the S2B guarantee.
    expect(page.locator("#pp-import-untouched")).to_contain_text("CDR")
    expect(page.locator("#pp-import-dividends")).to_contain_text("1")


def test_import_commit_writes_the_positions_into_the_table(page: Page, live_server_url: str):
    """Risk: the commit round-trip must land in the wallet the table renders."""
    _login_and_open(page, live_server_url)

    page.locator("#pp-import-btn").click()
    _attach_export(page)
    with page.expect_response(re.compile(r"/api/portfolio/import/preview")):
        page.locator("#pp-import-preview-btn").click()
    expect(page.locator("#pp-import-commit-btn")).to_be_visible()

    with page.expect_response(re.compile(r"/api/portfolio/import/commit")):
        page.locator("#pp-import-commit-btn").click()

    expect(page.locator("#pp-import-overlay")).to_be_hidden()
    expect(page.locator("#pp-tbody")).to_contain_text("ZZZ")
    # PKO was closed by the file, so it must be gone from the wallet.
    expect(page.locator("#pp-tbody")).not_to_contain_text("PKO")


def test_commit_refreshes_the_other_views_without_a_reload(page: Page, live_server_url: str):
    """Risk: treemap/calendar/chart render from their own caches.

    The single-position write path never clears them, so without an explicit
    reset the user commits an import and the other three tabs keep showing the
    pre-import wallet until they reload the page.
    """
    _login_and_open(page, live_server_url)
    # Populate the treemap cache BEFORE importing — otherwise a first-time fetch
    # would mask a stale cache and the test would pass for the wrong reason.
    page.locator('#pp-view-tabs .pp-view-tab[data-mode="treemap"]').click()
    expect(page.locator("#pp-treemap-wrap")).to_contain_text("PKO")
    page.locator('#pp-view-tabs .pp-view-tab[data-mode="table"]').click()

    page.locator("#pp-import-btn").click()
    _attach_export(page)
    with page.expect_response(re.compile(r"/api/portfolio/import/preview")):
        page.locator("#pp-import-preview-btn").click()
    with page.expect_response(re.compile(r"/api/portfolio/import/commit")):
        page.locator("#pp-import-commit-btn").click()
    expect(page.locator("#pp-import-overlay")).to_be_hidden()

    page.locator('#pp-view-tabs .pp-view-tab[data-mode="treemap"]').click()

    expect(page.locator("#pp-treemap-wrap")).to_contain_text("ZZZ")
    expect(page.locator("#pp-treemap-wrap")).not_to_contain_text("PKO")


def test_escape_closes_the_import_modal(page: Page, live_server_url: str):
    """Risk: the add-portfolio template this modal copies has no Escape handler."""
    _login_and_open(page, live_server_url)

    page.locator("#pp-import-btn").click()
    expect(page.locator("#pp-import-overlay")).to_be_visible()

    page.keyboard.press("Escape")

    expect(page.locator("#pp-import-overlay")).to_be_hidden()
