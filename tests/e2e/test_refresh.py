import re

from playwright.sync_api import Page, expect

_ADMIN_KEY = "e2e-admin-key"


def _login(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.locator(".landing-nav").get_by_role("button", name="Zaloguj się").click()
    page.get_by_role("button", name="Mam klucz API").click()
    page.get_by_label("Klucz API").fill(_ADMIN_KEY)
    page.locator("#api-key-panel").get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")


def test_refresh_with_existing_session_keeps_dashboard_functional(page: Page, live_server_url: str):
    errors = []
    page.on("pageerror", lambda exc: errors.append(exc))

    _login(page, live_server_url)
    page.get_by_role("button", name=re.compile("Następna")).click()
    expect(page.locator("#page-label")).to_have_text("Strona 2")
    page.get_by_placeholder("Ticker (np. PKO)").fill("PKO")

    page.reload()

    expect(page.get_by_role("columnheader", name="Spółka")).to_be_visible()
    expect(page.locator("#table-body tr")).to_have_count(20)
    assert errors == []

    with page.expect_request(re.compile(r"/announcements")):
        page.get_by_role("button", name="Filtruj").click()

    page.get_by_placeholder("Analizy od").click()
    expect(page.get_by_placeholder("Analizy od")).to_have_attribute("type", "datetime-local")

    page.get_by_role("button", name=re.compile("Następna")).click()
    expect(page.locator("#page-label")).to_have_text("Strona 2")


def test_refresh_preserves_page_and_filters(page: Page, live_server_url: str):
    _login(page, live_server_url)
    page.get_by_role("button", name=re.compile("Następna")).click()
    expect(page.locator("#page-label")).to_have_text("Strona 2")

    ticker_input = page.get_by_placeholder("Ticker (np. PKO)")
    ticker_input.fill("PKO")
    ticker_input.evaluate("el => el.blur()")
    expect(page.locator("#ac-ticker")).to_be_hidden()
    page.get_by_role("button", name="Filtruj").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")

    page.reload()

    expect(page.locator("#page-label")).to_have_text("Strona 1")
    expect(page.get_by_placeholder("Ticker (np. PKO)")).to_have_value("PKO")


def test_invalid_date_filter_does_not_throw_and_drops_param(page: Page, live_server_url: str):
    errors = []
    page.on("pageerror", lambda exc: errors.append(exc))

    _login(page, live_server_url)

    # Inject an unparseable value straight into #f-from (the native
    # datetime-local input would reject free text on fill), then notify the form.
    page.evaluate(
        "() => { const el = document.getElementById('f-from');"
        " el.value = 'not-a-date';"
        " el.dispatchEvent(new Event('change', { bubbles: true })); }"
    )

    with page.expect_request(re.compile(r"/announcements")) as req_info:
        page.get_by_role("button", name="Filtruj").click()

    # The garbage value must be dropped, not crash the request.
    assert "from=" not in req_info.value.url
    expect(page.locator("#table-body tr")).to_have_count(20)
    assert errors == []
