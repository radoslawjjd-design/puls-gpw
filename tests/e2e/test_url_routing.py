import re

from playwright.sync_api import Page, expect

from tests.e2e.conftest import e2e_login_email

_ADMIN_KEY = "e2e-admin-key"


def _login(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.locator(".landing-nav").get_by_role("button", name="Zaloguj się").click()
    page.get_by_role("button", name="Mam klucz API").click()
    page.get_by_label("Klucz API").fill(_ADMIN_KEY)
    page.locator("#api-key-panel").get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")


def _open_x_history(page: Page) -> None:
    page.get_by_role("button", name="Historia postów X").click()


def _persist_session_across_goto(page: Page) -> None:
    """sessionStorage must survive the next page.goto() for a deep-link/bookmark
    scenario to mean anything. page.reload() carries it forward reliably, but a
    full goto() to a new query string has been observed to drop sessionStorage
    in CI's headless Chromium (never reproduced locally) — an init script
    guarantees the same session values are present before the next document's
    script runs, regardless of that navigation-type quirk."""
    page.add_init_script(
        f"sessionStorage.setItem('apiKey', '{_ADMIN_KEY}'); sessionStorage.setItem('role', 'admin');"
    )


def test_view_switch_sequence_updates_url_and_is_back_navigable(
    page: Page, live_server_url: str
):
    # PUL-74: widoki per-user są JWT-only, a URL-state działa tylko na sesji
    # API-key (PUL-84) — sekwencja routingu testowana na widokach globalnych.
    _login(page, live_server_url)

    _open_x_history(page)
    expect(page).to_have_url(re.compile(r"view=x-history"))

    page.get_by_role("heading", name="Faro").click()
    expect(page).to_have_url(re.compile(r"/$"))
    expect(page.locator("#announcements-view")).to_be_visible()

    page.go_back()
    expect(page).to_have_url(re.compile(r"view=x-history"))
    expect(page.locator("#x-history-view")).to_be_visible()

    page.go_back()
    expect(page.locator("#announcements-view")).to_be_visible()
    expect(page).not_to_have_url(re.compile(r"view="))


def test_deep_link_to_portfolio_positions_lands_directly_on_view(page: Page, live_server_url: str):
    # PUL-74: portfolio jest per-user (JWT-only) — deep-link/bookmark odtwarza
    # widok na sesji e-mailowej (cookie + localStorage.hasSession przeżywają goto).
    e2e_login_email(page, live_server_url)
    # Ten sam headless-Chromium quirk co w _persist_session_across_goto: pełne
    # goto() potrafi zgubić storage — init-script gwarantuje flagę probe'a.
    page.add_init_script("localStorage.setItem('hasSession', '1')")

    page.goto(f"{live_server_url}?view=portfolio-positions")

    expect(page.locator("#portfolio-positions-view")).to_be_visible()
    expect(page.locator("#announcements-view")).to_be_hidden()


def test_deep_link_to_per_user_view_falls_back_for_api_key_session(
    page: Page, live_server_url: str
):
    """PUL-74: sesja API-key nie ma widoków per-user — deep-link ?view=my-wallet
    musi spaść na Ogłoszenia zamiast renderować martwy widok (401→doLogout)."""
    _login(page, live_server_url)
    _persist_session_across_goto(page)

    page.goto(f"{live_server_url}?view=my-wallet")

    expect(page.locator("#announcements-view")).to_be_visible()
    expect(page.locator("#my-wallet-view")).to_be_hidden()


def test_refresh_on_x_history_page_2_with_filter_restores_view_page_and_filter(
    page: Page, live_server_url: str
):
    _login(page, live_server_url)
    _open_x_history(page)

    page.get_by_role("combobox", name="Status").select_option("skipped")
    page.get_by_role("button", name="Filtruj").click()
    expect(page.locator("#xp-page-label")).to_have_text("Strona 1")

    page.get_by_role("button", name=re.compile("Następna")).click()
    expect(page.locator("#xp-page-label")).to_have_text("Strona 2")
    expect(page).to_have_url(re.compile(r"page=2"))

    page.reload()

    expect(page.locator("#x-history-view")).to_be_visible()
    expect(page.locator("#xp-page-label")).to_have_text("Strona 2")
    expect(page.get_by_role("combobox", name="Status")).to_have_value("skipped")
    expect(page).to_have_url(re.compile(r"view=x-history"))
    expect(page).to_have_url(re.compile(r"x_publish_status=skipped"))


def test_old_format_bookmark_resolves_to_announcements_page_2(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _persist_session_across_goto(page)

    page.goto(f"{live_server_url}?page=2&page_size=50")

    expect(page.locator("#announcements-view")).to_be_visible()
    expect(page.locator("#page-label")).to_have_text("Strona 2")
    expect(page.get_by_role("combobox", name="Rozmiar strony")).to_have_value("50")


def test_logout_resets_url_to_root(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_x_history(page)
    expect(page).to_have_url(re.compile(r"view=x-history"))

    page.get_by_role("button", name="admin").click()
    page.get_by_role("menuitem", name="Wyloguj").click()

    expect(page.locator("#login-screen")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/$"))
    expect(page).not_to_have_url(re.compile(r"view="))
