"""E2E tests — dividends tab (PUL-95 Phase 5)."""
import re
from datetime import date

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
    """Risk: the selector sits next to the view tabs, whose handler also binds this
    header — picking a year must refetch dividends, not switch the whole view."""
    _login_and_open(page, live_server_url)
    _open_dividends(page)

    with page.expect_response(re.compile(r"/api/portfolio/dividends.*year=2025")):
        page.get_by_label("Rok wypłat dywidend").select_option("2025")

    expect(page.locator("#pp-dividends-wrap")).to_be_visible()
    expect(page.get_by_label("Rok wypłat dywidend")).to_have_value("2025")


def test_the_year_selector_offers_every_year_the_wallet_existed(
    page: Page, live_server_url: str
):
    """Risk (PUL-117): one pill per year is unusable at twenty years, and a list built
    only from years that paid out cannot answer "did I get anything in 2023?" — the
    year simply isn't there. The dropdown spans inception → today, defaulting to all.

    Fixture: conftest._FAKE_INCEPTION is two years back, dividends in 2025 and 2026.
    """
    _login_and_open(page, live_server_url)
    _open_dividends(page)

    select = page.get_by_label("Rok wypłat dywidend")
    expect(select).to_have_value("")  # "Wszystkie" — every year, by default
    expect(page.locator("#pp-div-years select")).to_have_count(1)
    expect(page.locator("#pp-div-years button")).to_have_count(0)

    today = date.today()
    years = select.locator("option").all_inner_texts()
    assert years[0] == "Wszystkie"
    # Newest first, one per year, down to the wallet's first — including the year
    # in between that paid nothing.
    assert years[1:] == [str(y) for y in range(today.year, today.year - 3, -1)]


# ── PUL-120: the month selector ──────────────────────────────────────────────
#
# Fixture: KRU paid in June 2025 and June 2026, PKO in September 2026. January
# has nothing, which is the case the empty-period assertions lean on.


def test_month_switch_refetches_without_leaving_the_tab(page: Page, live_server_url: str):
    """Same risk the year selector carries: this header is adjacent to the view
    tabs, whose handler also binds here — picking a month must refetch dividends
    rather than switch the whole view."""
    _login_and_open(page, live_server_url)
    _open_dividends(page)

    with page.expect_response(re.compile(r"/api/portfolio/dividends.*month=9")):
        page.get_by_label("Miesiąc wypłat").select_option("9")

    expect(page.locator("#pp-dividends-wrap")).to_be_visible()
    expect(page.get_by_label("Miesiąc wypłat")).to_have_value("9")
    # September paid PKO and nothing else.
    expect(page.locator("#pp-div-by-ticker")).to_contain_text("PKO")
    expect(page.locator("#pp-div-by-ticker")).not_to_contain_text("KRU")


def test_a_month_without_a_year_spans_every_year(page: Page, live_server_url: str):
    """Both selectors accept 'Wszystkie' independently, so "every June on record"
    is reachable. The request must carry the month and no year."""
    _login_and_open(page, live_server_url)
    _open_dividends(page)

    with page.expect_response(re.compile(r"/api/portfolio/dividends")) as caught:
        page.get_by_label("Miesiąc wypłat").select_option("6")

    url = caught.value.url
    assert "month=6" in url, url
    assert "year=" not in url, f"year should be absent while 'Wszystkie' is selected: {url}"
    expect(page.locator("#pp-div-by-ticker")).to_contain_text("KRU")


def test_an_empty_month_still_lets_the_user_leave_it(page: Page, live_server_url: str):
    """The PUL-100 lesson at month granularity, where empty periods are routine
    rather than rare: a selector rebuilt from the filtered rows would lose its
    options exactly when the user needs them to escape."""
    _login_and_open(page, live_server_url)
    _open_dividends(page)

    with page.expect_response(re.compile(r"/api/portfolio/dividends.*month=1")):
        page.get_by_label("Miesiąc wypłat").select_option("1")

    expect(page.locator("#pp-div-by-ticker")).to_contain_text("Brak dywidend")
    # Both selectors survive the empty period with their full option lists.
    expect(page.get_by_label("Miesiąc wypłat").locator("option")).to_have_count(13)
    assert page.get_by_label("Rok wypłat dywidend").locator("option").count() > 1

    with page.expect_response(re.compile(r"/api/portfolio/dividends")):
        page.get_by_label("Miesiąc wypłat").select_option("")
    expect(page.locator("#pp-div-by-ticker")).to_contain_text("KRU")


def test_year_and_month_narrow_together(page: Page, live_server_url: str):
    """The fourth combination: both selectors set at once."""
    _login_and_open(page, live_server_url)
    _open_dividends(page)

    with page.expect_response(re.compile(r"/api/portfolio/dividends.*year=2025")):
        page.get_by_label("Rok wypłat dywidend").select_option("2025")
    with page.expect_response(re.compile(r"/api/portfolio/dividends.*month=6")):
        page.get_by_label("Miesiąc wypłat").select_option("6")

    expect(page.locator("#pp-div-by-ticker")).to_contain_text("KRU")
    # June 2025 is one payout, not the two June holds across both years.
    expect(page.locator("#pp-div-tiles")).to_contain_text("1")


def test_switching_away_hides_the_dividends_panel(page: Page, live_server_url: str):
    """Risk: a panel missing from the display block stays visible over the next view."""
    _login_and_open(page, live_server_url)
    _open_dividends(page)
    expect(page.locator("#pp-dividends-wrap")).to_be_visible()

    page.locator('#pp-view-tabs .pp-view-tab[data-mode="table"]').click()

    expect(page.locator("#pp-dividends-wrap")).to_be_hidden()
    expect(page.locator("#pp-table-wrap")).to_be_visible()
