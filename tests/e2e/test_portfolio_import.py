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

# XTB closes the sheet with a Total whose Amount is the free-cash balance. Kept
# out of _ROWS because normalize_operations treats it as a terminator, and the
# preview reads it through its own path.
_TOTAL_ROW = ["Total", None, None, None, 1234.56, None, None, None]


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
    ws.append(_TOTAL_ROW)
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


# ── create-wallet-with-import (follow-up to PUL-95) ──────────────────────────


def test_create_wallet_modal_offers_an_optional_import(page: Page, live_server_url: str):
    """Risk: a new user has no wallet, so the plain import button has nowhere to write."""
    _login_and_open(page, live_server_url)

    page.locator("#pp-add-portfolio-btn").click()

    expect(page.locator("#pp-add-portfolio-overlay")).to_be_visible()
    expect(page.locator("#pp-new-broker")).to_be_visible()
    expect(page.locator("#pp-new-file")).to_be_visible()
    # The wallet-type control is still the primary input; import is the extra.
    expect(page.locator("#pp-portfolio-type-select")).to_be_visible()


def test_creating_a_wallet_with_a_file_imports_in_one_step(page: Page, live_server_url: str):
    """Risk: the two calls are chained client-side, so a broken chain silently
    leaves an empty wallet with no signal that the file was ignored."""
    _login_and_open(page, live_server_url)

    page.locator("#pp-add-portfolio-btn").click()
    # 'glowny' already exists in the fixture and would 409 before reaching import.
    page.locator("#pp-portfolio-type-select").select_option("ikze")
    page.locator("#pp-new-file").set_input_files({
        "name": "export.xlsx", "mimeType": _XLSX_MIME, "buffer": _export_bytes(),
    })

    with page.expect_response(re.compile(r"/api/portfolio/import/commit")):
        page.locator("#pp-portfolio-modal-save").click()

    expect(page.locator("#pp-add-portfolio-overlay")).to_be_hidden()
    expect(page.locator("#pp-tbody")).to_contain_text("ZZZ")


def test_creating_a_wallet_without_a_file_does_not_call_the_import(page: Page, live_server_url: str):
    """Risk: import must stay optional — the plain create path has to remain untouched."""
    _login_and_open(page, live_server_url)
    calls = []
    page.on("request", lambda r: calls.append(r.url) if "/import/" in r.url else None)

    page.locator("#pp-add-portfolio-btn").click()
    page.locator("#pp-portfolio-type-select").select_option("ike")
    with page.expect_response(re.compile(r"/api/portfolio/wallets")):
        page.locator("#pp-portfolio-modal-save").click()

    expect(page.locator("#pp-add-portfolio-overlay")).to_be_hidden()
    assert calls == [], f"import wywolany mimo braku pliku: {calls}"


def test_with_no_wallets_the_import_button_opens_the_create_wallet_modal(
    page: Page, live_server_url: str
):
    """Risk: with zero wallets the import button previously had no target at all.

    The wallet list is emptied at the network layer rather than in the shared
    conftest — PUL-90 showed that adding entities there destabilizes the suite.
    """
    page.route("**/api/portfolio/wallets",
               lambda route: route.fulfill(status=200, content_type="application/json", body="[]")
               if route.request.method == "GET" else route.continue_())
    try:
        e2e_login_email(page, live_server_url)
        page.get_by_role("button", name="Mój portfel").click()

        expect(page.locator("#pp-import-btn")).to_be_visible()
        page.locator("#pp-import-btn").click()

        # Routed to wallet creation, not to the import modal that has no target.
        expect(page.locator("#pp-add-portfolio-overlay")).to_be_visible()
        expect(page.locator("#pp-import-overlay")).to_be_hidden()
        expect(page.locator("#pp-new-file")).to_be_visible()
    finally:
        page.unroute_all(behavior="ignoreErrors")


def test_preview_discloses_the_free_cash_the_commit_will_write(page: Page, live_server_url: str):
    """Risk: the commit writes a cash position, so the preview has to say so.

    The whole point of the preview is that nothing lands unannounced — a PLN row
    appearing in the portfolio out of nowhere would be indistinguishable from a
    bug. The synthesized workbook carries a Total row, which is where XTB states
    the balance.
    """
    _login_and_open(page, live_server_url)
    page.locator("#pp-import-btn").click()
    page.locator("#pp-import-file").set_input_files(
        files=[{"name": "xtb.xlsx", "mimeType": _XLSX_MIME, "buffer": _export_bytes()}]
    )

    with page.expect_response(re.compile(r"/api/portfolio/import/preview")):
        page.locator("#pp-import-preview-btn").click()

    cash = page.locator("#pp-import-cash")
    expect(cash).to_be_visible()
    expect(cash).to_contain_text("Wolne środki")
    # \s, not a literal space: pl-PL groups thousands with a non-breaking space.
    expect(cash).to_contain_text(re.compile(r"1\s?234,56"))
