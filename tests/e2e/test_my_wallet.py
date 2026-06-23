from playwright.sync_api import Page, expect

_USER_KEY = "e2e-user-key"


def _login(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.get_by_label("Klucz API").fill(_USER_KEY)
    page.get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")


def _open_my_wallet(page: Page) -> None:
    page.get_by_role("button", name="user").click()
    page.get_by_role("menuitem", name="Obserwowane").click()


def test_added_ticker_persists_across_reload_and_filters_announcements(
    page: Page, live_server_url: str
):
    """Risk: My Wallet's only real value is that a watched ticker survives a
    genuine page reload and narrows announcements to just that ticker — both
    exist solely in the rendered UI + localStorage client id, not provable by
    a unit test against db/bigquery.py or src/api.py in isolation."""
    _login(page, live_server_url)
    _open_my_wallet(page)

    wallet_view = page.locator("#my-wallet-view")
    expect(wallet_view.get_by_text("Nie obserwujesz jeszcze żadnego tickera.")).to_be_visible()

    wallet_view.get_by_placeholder("Ticker (np. PKO)").fill("PKO")
    wallet_view.get_by_role("button", name="Dodaj").click()

    expect(page.get_by_role("button", name="Usuń PKO z obserwowanych")).to_be_visible()
    expect(page.locator("#my-wallet-table-body")).to_contain_text("PKO SA")

    page.reload()

    expect(page.get_by_role("button", name="Usuń PKO z obserwowanych")).to_be_visible()
    expect(page.locator("#my-wallet-table-body")).to_contain_text("PKO SA")
