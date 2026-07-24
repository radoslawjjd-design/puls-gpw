from playwright.sync_api import Page, expect

from tests.e2e.conftest import e2e_login_email



def _login(page: Page, base_url: str) -> None:
    # PUL-74: widoki per-user są JWT-only — logowanie przez formularz e-mail.
    e2e_login_email(page, base_url)


def _open_portfolio(page: Page) -> None:
    page.get_by_role("button", name="Mój portfel").click()
    # PUL-90: default tab is read-only "Wszystkie" — select Główny for the editable view.
    page.locator("#pp-portfolio-tabs .pp-portfolio-tab", has_text="Główny").click()


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

    expect(page.locator("#pp-edit-overlay")).to_be_visible()
    expect(page.locator("#pp-edit-title")).to_contain_text("PKO")
    expect(page.locator("#pp-edit-shares")).to_have_value("10")
    expect(page.locator("#pp-edit-price")).to_have_value("40")

    page.locator("#pp-edit-shares").fill("20")
    page.locator("#pp-edit-save-btn").click()

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


def test_wszystkie_aggregate_view_is_default_and_read_only(page: Page, live_server_url: str):
    """PUL-90: 'Wszystkie' is the first + default tab and shows positions in a read-only
    aggregate (no per-row edit/delete, no 'Dodaj pozycję'); selecting a wallet restores
    editing and scopes back to that wallet."""
    _login(page, live_server_url)
    page.get_by_role("button", name="Mój portfel").click()

    tabs = page.locator("#pp-portfolio-tabs")
    first_tab = tabs.locator(".pp-portfolio-tab").first
    expect(first_tab).to_have_text("Wszystkie")
    # default on entry: the aggregate tab is the active one
    expect(page.locator(".pp-portfolio-tab.active")).to_have_text("Wszystkie")

    # aggregate positions render + summary visible
    expect(page.locator("#pp-tbody")).to_contain_text("PKO")
    expect(page.locator("#pp-summary")).to_be_visible()

    # read-only: no per-row edit/delete controls, no "Dodaj pozycję"
    expect(page.locator("#pp-tbody button", has_text="Edytuj")).to_have_count(0)
    expect(page.locator("#pp-tbody button", has_text="Usuń")).to_have_count(0)
    expect(page.locator("#pp-add-toggle-btn")).to_be_hidden()

    # selecting the Główny wallet restores editing and the add-position toggle
    tabs.locator(".pp-portfolio-tab", has_text="Główny").click()
    expect(page.locator("#pp-tbody button", has_text="Edytuj").first).to_be_visible()
    expect(page.locator("#pp-add-toggle-btn")).to_be_visible()
