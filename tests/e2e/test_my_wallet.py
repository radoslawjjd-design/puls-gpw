from playwright.sync_api import Page, expect

from tests.e2e.conftest import e2e_login_email



def _login(page: Page, base_url: str) -> None:
    # PUL-74: widoki per-user są JWT-only — logowanie przez formularz e-mail.
    e2e_login_email(page, base_url)


def _open_my_wallet(page: Page) -> None:
    page.get_by_role("button", name="Obserwowane").click()


def test_added_ticker_persists_across_reload_and_filters_announcements(
    page: Page, live_server_url: str
):
    """Risk: My Wallet's only real value is that a watched ticker survives a
    genuine page reload — the persistence now comes from BQ keyed by the JWT
    uid (PUL-74), not browser state. Po reloadzie sesja JWT bootuje do widoku
    Ogłoszeń (URL-state dla JWT = PUL-84), więc test wraca do Obserwowanych
    nawigacją i dopiero wtedy asertuje trwałość."""
    _login(page, live_server_url)
    _open_my_wallet(page)

    wallet_view = page.locator("#my-wallet-view")
    expect(wallet_view.get_by_text("Nie obserwujesz jeszcze żadnego tickera.")).to_be_visible()

    wallet_view.get_by_placeholder("Ticker (np. PKO)").fill("PKO")
    wallet_view.get_by_role("button", name="Dodaj").click()

    expect(page.get_by_role("button", name="Usuń PKO z obserwowanych")).to_be_visible()
    expect(page.locator("#my-wallet-table-body")).to_contain_text("PKO SA")

    page.reload()
    expect(page.locator("#announcements-view")).to_be_visible()
    _open_my_wallet(page)

    expect(page.get_by_role("button", name="Usuń PKO z obserwowanych")).to_be_visible()
    expect(page.locator("#my-wallet-table-body")).to_contain_text("PKO SA")


def test_watchlist_is_isolated_between_users(page: Page, live_server_url: str):
    """Risk (PUL-74): kryterium ticketu — user B nie może zobaczyć watchlisty
    usera A. Dwie realne sesje e-mailowe w jednej przeglądarce; izolacja musi
    wynikać z uid w JWT, nie ze stanu przeglądarki."""
    e2e_login_email(page, live_server_url)
    _open_my_wallet(page)

    wallet_view = page.locator("#my-wallet-view")
    wallet_view.get_by_placeholder("Ticker (np. PKO)").fill("PKO")
    wallet_view.get_by_role("button", name="Dodaj").click()
    expect(page.get_by_role("button", name="Usuń PKO z obserwowanych")).to_be_visible()

    page.get_by_role("button", name="użytkownik").click()
    page.get_by_role("menuitem", name="Wyloguj").click()

    e2e_login_email(page, live_server_url)  # świeży, unikalny e-mail = inny uid
    _open_my_wallet(page)

    expect(
        page.locator("#my-wallet-view").get_by_text("Nie obserwujesz jeszcze żadnego tickera.")
    ).to_be_visible()
    expect(page.get_by_role("button", name="Usuń PKO z obserwowanych")).not_to_be_visible()
