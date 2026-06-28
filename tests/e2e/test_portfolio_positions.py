from playwright.sync_api import Page, expect

from tests.e2e.conftest import _portfolio_positions_store

_USER_KEY = "e2e-user-key"


def _login(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.get_by_label("Klucz API").fill(_USER_KEY)
    page.get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")


def _open_portfolio(page: Page) -> None:
    page.get_by_role("button", name="Mój portfel").click()


def _add_position(page: Page, ticker: str, company: str, shares: str, price: str) -> None:
    pp = page.locator("#portfolio-positions-view")
    pp.get_by_role("button", name="Dodaj pozycję").click()
    pp.get_by_placeholder("Ticker (np. PKO)").fill(ticker)
    pp.get_by_placeholder("Nazwa spółki").fill(company)
    pp.get_by_placeholder("Ilość akcji").fill(shares)
    pp.get_by_placeholder("Śr. cena zakupu (PLN)").fill(price)
    pp.get_by_role("button", name="Dodaj").click()


def test_user_can_add_position_and_see_it_in_table(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_portfolio(page)
    _add_position(page, "PKO", "PKO BP SA", "10", "40.00")

    expect(page.locator("#pp-tbody")).to_contain_text("PKO")
    expect(page.locator("#pp-tbody")).to_contain_text("10")


def test_user_can_edit_position_and_see_updated_values(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_portfolio(page)
    _add_position(page, "PKO", "PKO BP SA", "10", "40.00")

    expect(page.locator("#pp-tbody")).to_contain_text("PKO")

    page.get_by_role("button", name="Edytuj").click()

    expect(page.locator("#pp-shares-input")).to_have_value("10")
    expect(page.locator("#pp-price-input")).to_have_value("40")
    expect(page.locator("#pp-ticker-label")).to_have_text("PKO")

    page.locator("#pp-shares-input").fill("20")
    page.get_by_role("button", name="Zapisz zmiany").click()

    expect(page.locator("#pp-tbody")).to_contain_text("20")


def test_user_can_delete_position_with_confirmation(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_portfolio(page)
    _add_position(page, "PKO", "PKO BP SA", "10", "40.00")

    expect(page.locator("#pp-tbody")).to_contain_text("PKO")

    page.on("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Usuń").click()

    expect(page.locator("#pp-tbody")).not_to_contain_text("PKO")


def test_positions_show_dashes_when_no_price_data(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_portfolio(page)

    client_id = page.evaluate("() => localStorage.getItem('watchlist_client_id')")

    _portfolio_positions_store[client_id] = [{
        "ticker": "XYZ", "company_name": "Firma XYZ",
        "shares": 5.0, "avg_buy_price": 30.0,
        "current_price": None, "daily_change_pct": None,
        "price_as_of": None,
    }]

    page.get_by_role("button", name="Obserwowane").click()
    _open_portfolio(page)

    expect(page.locator("#pp-tbody")).to_contain_text("XYZ")
    expect(page.locator("#pp-tbody")).to_contain_text("—")
