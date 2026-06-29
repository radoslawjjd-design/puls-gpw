"""E2E tests — ETF/ETC/ETN portfolio integration (PUL-67).

Risk 1: ETF ticker must be accepted by POST /api/portfolio/positions (not HTTP 422)
        and the added position must appear in the portfolio table.
Risk 2: ETF tickers must appear in the ticker autocomplete datalist so users can
        discover and select them when building a portfolio.
Risk 3: Selecting an ETF name in the company field must auto-fill the ticker field
        (reverse lookup via _etfInstrumentsMap, without hitting /announcements).

Seed: tests/e2e/test_portfolio_positions.py
"""
from playwright.sync_api import Page, expect

_USER_KEY = "e2e-user-key"


def _login(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.get_by_label("Klucz API").fill(_USER_KEY)
    page.get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")


def _open_portfolio(page: Page) -> None:
    page.get_by_role("button", name="Mój portfel").click()


def _open_add_form(page: Page) -> None:
    page.locator("#portfolio-positions-view").get_by_role("button", name="Dodaj pozycję").click()


def _add_position(page: Page, ticker: str, company: str, shares: str, price: str) -> None:
    pp = page.locator("#portfolio-positions-view")
    pp.get_by_role("button", name="Dodaj pozycję").click()
    pp.get_by_placeholder("Ticker (np. PKO)").fill(ticker)
    pp.get_by_placeholder("Nazwa spółki / ETF").fill(company)
    pp.get_by_placeholder("Ilość akcji").fill(shares)
    pp.get_by_placeholder("Śr. cena zakupu (PLN)").fill(price)
    pp.get_by_role("button", name="Dodaj", exact=True).click()


def test_user_can_add_etf_position_and_see_it_in_table(page: Page, live_server_url: str):
    """Risk: ETF ticker must be accepted by POST /api/portfolio/positions (not HTTP 422)
    and the added position must appear in the portfolio positions table.
    Breaks if ETFBW20TR is absent from list_distinct_tickers → validation returns 422."""
    _login(page, live_server_url)
    _open_portfolio(page)
    _add_position(page, "ETFBW20TR", "ETFBW20TR", "5", "600.00")

    expect(page.locator("#pp-tbody")).to_contain_text("ETFBW20TR")
    expect(page.locator("#pp-tbody")).to_contain_text("5")


def test_etf_tickers_included_in_portfolio_ticker_autocomplete(page: Page, live_server_url: str):
    """Risk: ETF instruments must appear in the #dl-tickers datalist so users can
    discover them in the portfolio form — proving list_distinct_tickers UNION and
    /autocomplete/etf-instruments are both wired correctly after page load.
    Breaks if ETFBW20TR is absent from the list_distinct_tickers mock."""
    _login(page, live_server_url)

    # Wait until the async autocomplete fetch populates the datalist
    page.wait_for_function(
        "Array.from(document.querySelector('#dl-tickers').options)"
        ".some(o => o.value === 'ETFBW20TR')"
    )

    options = page.evaluate(
        "Array.from(document.querySelector('#dl-tickers').options).map(o => o.value)"
    )
    assert "ETFBW20TR" in options


def test_selecting_etf_name_fills_ticker_field(page: Page, live_server_url: str):
    """Risk: typing an ETF name in the company field and selecting from dropdown
    must auto-fill the ticker field via _etfInstrumentsMap (not /announcements).
    Breaks if list_etf_instruments_for_autocomplete is not mocked or ETF reverse
    lookup is missing from _ppWireAcCrossFill."""
    _login(page, live_server_url)
    _open_portfolio(page)
    _open_add_form(page)

    pp = page.locator("#portfolio-positions-view")

    # Wait for ETF instruments to load (same async batch as _acTickers)
    page.wait_for_function(
        "Array.from(document.querySelector('#dl-tickers').options)"
        ".some(o => o.value === 'ETFBW20TR')"
    )

    # Type ETF name into the company field — triggers the ac-dropdown
    company_input = pp.locator("#pp-company-input")
    company_input.fill("ETFBW20TR")

    # Select from the dropdown suggestion
    pp.locator("#ac-pp-company .ac-item", has_text="ETFBW20TR").click()

    # Ticker field must be auto-filled by the ETF reverse lookup
    expect(pp.locator("#pp-ticker-input")).to_have_value("ETFBW20TR")
