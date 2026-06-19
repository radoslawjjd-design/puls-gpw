import re

from playwright.sync_api import Page, expect

_ADMIN_KEY = "e2e-admin-key"


def _login(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.get_by_label("Klucz API").fill(_ADMIN_KEY)
    page.get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")


def test_ticker_and_company_datalists_populated(page: Page, live_server_url: str):
    _login(page, live_server_url)
    # Wait for the async autocomplete fetch to complete
    page.wait_for_function("document.querySelector('#dl-tickers').options.length > 0")
    page.wait_for_function("document.querySelector('#dl-companies').options.length > 0")

    tickers_count = page.evaluate("document.querySelector('#dl-tickers').options.length")
    companies_count = page.evaluate("document.querySelector('#dl-companies').options.length")

    assert tickers_count > 0
    assert companies_count > 0


def test_event_type_datalist_populated(page: Page, live_server_url: str):
    _login(page, live_server_url)
    # dl-event-types is populated synchronously from the static JS map
    page.wait_for_function("document.querySelector('#dl-event-types').options.length > 0")

    event_count = page.evaluate("document.querySelector('#dl-event-types').options.length")
    assert event_count >= 5


def test_event_type_label_translates_to_code_in_filter(page: Page, live_server_url: str):
    _login(page, live_server_url)
    page.wait_for_function("document.querySelector('#dl-event-types').options.length > 0")

    # Fill the event type input with a Polish label produced by _toLabel()
    page.locator("#f-event-type").fill("Wyniki sprzedazowe")

    with page.expect_request(re.compile(r"/announcements")) as req_info:
        page.get_by_role("button", name="Filtruj").click()

    assert "event_type=wyniki_sprzedazowe" in req_info.value.url
