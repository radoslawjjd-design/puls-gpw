"""E2E tests — dividends tab (PUL-95 Phase 5)."""
import re

from playwright.sync_api import Page, expect

from tests.e2e.conftest import e2e_login_email


def _login_and_open(page: Page, base_url: str) -> None:
    e2e_login_email(page, base_url)
    page.get_by_role("button", name="Mój portfel").click()
    page.locator("#pp-portfolio-tabs .pp-portfolio-tab", has_text="Główny").click()


def _open_dividends(page: Page) -> None:
    with page.expect_response(re.compile(r"/api/portfolio/dividends")):
        page.locator('#pp-view-tabs .pp-view-tab[data-mode="dividends"]').click()


def test_dividends_tab_fetches_and_renders(page: Page, live_server_url: str):
    """Risk: the mode handler is two adjacent blocks.

    Wiring only the display block shows the panel and never fetches, so the tab
    opens onto an empty box — the failure plan-review F1 was raised about.
    """
    _login_and_open(page, live_server_url)

    _open_dividends(page)

    expect(page.locator("#pp-dividends-wrap")).to_be_visible()
    expect(page.locator("#pp-div-by-ticker")).to_contain_text("KRU")


def test_dividends_tiles_show_gross_tax_and_net_separately(page: Page, live_server_url: str):
    """Risk: IKZE has zero withholding tax, so one number makes a correct account look broken."""
    _login_and_open(page, live_server_url)

    _open_dividends(page)

    tiles = page.locator("#pp-div-tiles")
    expect(tiles).to_contain_text("Brutto")
    expect(tiles).to_contain_text("Podatek")
    expect(tiles).to_contain_text("Netto")
    # Named explicitly: the export carries no scrip dividends, so the total must
    # not read as the whole picture.
    expect(page.locator("#pp-dividends-wrap")).to_contain_text("gotówkowe")


def test_year_switch_refetches_without_leaving_the_tab(page: Page, live_server_url: str):
    """Risk: the year pills reuse .pp-view-tab, which the mode handler also binds."""
    _login_and_open(page, live_server_url)
    _open_dividends(page)

    with page.expect_response(re.compile(r"/api/portfolio/dividends.*year=2025")):
        page.locator('#pp-div-years .pp-view-tab[data-year="2025"]').click()

    expect(page.locator("#pp-dividends-wrap")).to_be_visible()
    expect(page.locator('#pp-div-years .pp-view-tab[data-year="2025"]')).to_have_class(
        re.compile(r"active")
    )


def test_switching_away_hides_the_dividends_panel(page: Page, live_server_url: str):
    """Risk: a panel missing from the display block stays visible over the next view."""
    _login_and_open(page, live_server_url)
    _open_dividends(page)
    expect(page.locator("#pp-dividends-wrap")).to_be_visible()

    page.locator('#pp-view-tabs .pp-view-tab[data-mode="table"]').click()

    expect(page.locator("#pp-dividends-wrap")).to_be_hidden()
    expect(page.locator("#pp-table-wrap")).to_be_visible()
