import re

from playwright.sync_api import Page, expect

from tests.e2e.conftest import _ADMIN_KEY, e2e_login_email


def _open_settings(page: Page) -> None:
    # Profile menu trigger is named after the role badge ("Użytkownik").
    page.get_by_role("button", name="użytkownik").click()
    page.get_by_role("menuitem", name="Ustawienia").click()
    expect(page).to_have_url(re.compile(r"\?view=settings"))
    expect(page.locator("#settings-view")).to_be_visible()


def test_settings_notifications_default_off_with_description(page: Page, live_server_url: str):
    """A fresh user opts in explicitly: the email switch defaults to off and the
    description explains what enabling it does."""
    e2e_login_email(page, live_server_url)
    _open_settings(page)

    switch = page.get_by_role("switch", name="Powiadomienia email")
    expect(switch).to_be_visible()
    expect(switch).not_to_be_checked()
    expect(
        page.get_by_text("Po włączeniu będziesz otrzymywać powiadomienia na swój adres email")
    ).to_be_visible()


def test_settings_email_toggle_persists_across_reload(page: Page, live_server_url: str):
    """Risk: the whole point of the preference is that it survives a real page
    reload — the state comes from BQ keyed by the JWT uid, not browser state."""
    e2e_login_email(page, live_server_url)
    _open_settings(page)

    switch = page.get_by_role("switch", name="Powiadomienia email")
    expect(switch).not_to_be_checked()

    switch.check()
    expect(switch).to_be_checked()
    # Wait for the optimistic POST to settle (the switch re-enables in finally)
    # before reloading, so the reload reads the persisted value deterministically.
    expect(switch).to_be_enabled()

    page.reload()
    expect(page.locator("#settings-view")).to_be_visible()
    switch = page.get_by_role("switch", name="Powiadomienia email")
    expect(switch).to_be_checked()

    switch.uncheck()
    expect(switch).not_to_be_checked()
    expect(switch).to_be_enabled()

    page.reload()
    expect(page.locator("#settings-view")).to_be_visible()
    expect(page.get_by_role("switch", name="Powiadomienia email")).not_to_be_checked()


def test_settings_view_is_hidden_after_switching_to_another_tab(page: Page, live_server_url: str):
    """Regression: the settings view must not linger below the table after the
    user navigates to another tab — every sibling view has to hide it."""
    e2e_login_email(page, live_server_url)
    _open_settings(page)
    expect(page.locator("#settings-view")).to_be_visible()

    page.get_by_role("button", name="Ogłoszenia").click()
    expect(page.locator("#announcements-view")).to_be_visible()
    expect(page.locator("#settings-view")).to_be_hidden()


def test_settings_save_failure_reverts_with_inline_error(page: Page, live_server_url: str):
    """Risk (optimistic save): a failed POST must revert the switch and show an
    inline message, not silently leave the UI lying about the stored state."""
    e2e_login_email(page, live_server_url)
    _open_settings(page)

    switch = page.get_by_role("switch", name="Powiadomienia email")
    expect(switch).not_to_be_checked()
    expect(switch).to_be_enabled()

    def _fail_post(route):
        if route.request.method == "POST":
            route.fulfill(status=500, content_type="application/json", body='{"detail":"boom"}')
        else:
            route.continue_()

    page.route("**/api/notifications/settings", _fail_post)
    # .click() (not .check()) — the handler reverts the state, so asserting a
    # final "checked" would fight the revert.
    switch.click()

    expect(page.get_by_text("Nie udało się zapisać, spróbuj ponownie.")).to_be_visible()
    expect(switch).not_to_be_checked()
    page.unroute("**/api/notifications/settings", _fail_post)


def test_settings_entry_hidden_for_api_key_session(page: Page, live_server_url: str):
    """Settings/Powiadomienia is a real-account feature — an API-key (admin-tool)
    session must not see the 'Ustawienia' entry."""
    page.goto(live_server_url)
    page.locator(".landing-nav").get_by_role("button", name="Zaloguj się").click()
    page.get_by_role("button", name="Mam klucz API").click()
    page.get_by_label("Klucz API").fill(_ADMIN_KEY)
    page.locator("#api-key-panel").get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")

    page.locator("#profile-menu-btn").click()
    expect(page.get_by_role("menuitem", name="Wyloguj")).to_be_visible()  # menu is open
    expect(page.get_by_role("menuitem", name="Ustawienia")).not_to_be_visible()
