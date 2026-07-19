from playwright.sync_api import Page, expect

from tests.e2e.conftest import E2E_ADMIN_EMAIL, e2e_login_email


def _login(page: Page, base_url: str, admin: bool) -> None:
    # PUL-74: widoki per-user są JWT-only — logowanie przez formularz e-mail.
    e2e_login_email(page, base_url, email=E2E_ADMIN_EMAIL if admin else None)


def _open_my_wallet_with_pko(page: Page) -> None:
    page.get_by_role("button", name="Obserwowane").click()
    wallet_view = page.locator("#my-wallet-view")
    wallet_view.get_by_placeholder("Ticker (np. PKO)").fill("PKO")
    wallet_view.get_by_role("button", name="Dodaj").click()
    expect(page.get_by_role("button", name="Usuń PKO z obserwowanych")).to_be_visible()


def test_admin_sees_populated_sentiment_bar(page: Page, live_server_url: str):
    """Risk (PUL-82): the sentiment bar must render real aggregated data for admins —
    the shipped stub always showed zeros because the backend stripped sentiment/score.
    Proves the full chain: BQ columns → admin branch → frontend aggregation."""
    _login(page, live_server_url, admin=True)
    _open_my_wallet_with_pko(page)

    bar = page.locator("#wl-sentiment-summary")
    expect(bar).to_be_visible()
    expect(bar).to_contain_text("Ostatnie 7 dni")
    expect(bar).to_contain_text("Pozytywny: 1")
    expect(bar).to_contain_text("Śr. score: 85")


def test_user_never_sees_sentiment_bar(page: Page, live_server_url: str):
    """Risk (PUL-82): sentiment/score are admin-only by app convention — a regular
    user must not see the bar at all, even with watchlist announcements present."""
    _login(page, live_server_url, admin=False)
    _open_my_wallet_with_pko(page)

    expect(page.locator("#my-wallet-table-body")).to_contain_text("PKO SA")
    expect(page.locator("#wl-sentiment-summary")).not_to_be_visible()


def test_admin_bar_does_not_survive_relogin_as_user(page: Page, live_server_url: str):
    """Risk (PUL-82, found in manual testing): logout does not reload the page, so a
    bar rendered for an admin persisted into a subsequent user session via the
    _watchlistFetched guard. The user must not see the stale admin bar."""
    _login(page, live_server_url, admin=True)
    _open_my_wallet_with_pko(page)
    expect(page.locator("#wl-sentiment-summary")).to_be_visible()

    page.get_by_role("button", name="admin").click()
    page.get_by_role("menuitem", name="Wyloguj").click()

    # PUL-74: watchlisty są odizolowane per uid — nowy user ma pustą listę,
    # więc dodaje PKO sam; stały pasek admina i tak nie może się pojawić.
    _login(page, live_server_url, admin=False)
    _open_my_wallet_with_pko(page)

    expect(page.locator("#my-wallet-table-body")).to_contain_text("PKO SA")
    expect(page.locator("#wl-sentiment-summary")).not_to_be_visible()
