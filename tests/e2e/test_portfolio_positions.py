from playwright.sync_api import Page, expect

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
    pp.get_by_role("button", name="Dodaj", exact=True).click()


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

    page.locator("#pp-tbody tr", has_text="PKO").get_by_role("button", name="Edytuj").click()

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
    page.locator("#pp-tbody tr", has_text="PKO").get_by_role("button", name="Usuń").click()

    expect(page.locator("#pp-tbody")).not_to_contain_text("PKO")


def test_positions_show_dashes_when_no_price_data(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_portfolio(page)

    # CDR from _FAKE_PORTFOLIO_POSITIONS has current_price=None — all price columns show "—"
    expect(page.locator("#pp-tbody")).to_contain_text("CDR")
    expect(page.locator("#pp-tbody")).to_contain_text("—")
