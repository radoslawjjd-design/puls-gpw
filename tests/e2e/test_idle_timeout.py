from playwright.sync_api import Page, expect

_ADMIN_KEY = "e2e-admin-key"


def _login(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.clock.install()
    # Freeze the clock so each fast_forward jump is the only time that
    # passes — without pause_at, the virtual clock keeps ticking at
    # real-time speed and the live countdown drifts during assertion polling.
    page.clock.pause_at(page.evaluate("() => Date.now()"))
    page.get_by_label("Klucz API").fill(_ADMIN_KEY)
    page.get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")


def test_warning_appears_with_live_countdown_at_threshold(page: Page, live_server_url: str):
    _login(page, live_server_url)
    expect(page.locator("#idle-warning-overlay")).to_be_hidden()

    page.clock.fast_forward("08:00")  # SESSION_IDLE_MINUTES - SESSION_WARNING_MINUTES
    expect(page.locator("#idle-warning-overlay")).to_be_visible()
    expect(page.locator("#idle-countdown")).to_have_text("2:00")


def test_stay_logged_in_keeps_session_alive_past_original_deadline(page: Page, live_server_url: str):
    _login(page, live_server_url)
    page.clock.fast_forward("08:00")
    expect(page.locator("#idle-warning-overlay")).to_be_visible()

    page.get_by_role("button", name="Zostań zalogowany").click()
    expect(page.locator("#idle-warning-overlay")).to_be_hidden()

    # Original deadline (10:00 from first activity) has now passed, but the
    # "stay logged in" click reset the clock — session must still be alive.
    page.clock.fast_forward("02:30")
    expect(page.locator("#dashboard-screen")).to_be_visible()
    expect(page.locator("#login-screen")).to_be_hidden()


def test_activity_before_threshold_prevents_warning(page: Page, live_server_url: str):
    _login(page, live_server_url)
    page.clock.fast_forward("07:00")
    page.mouse.move(10, 10)
    page.clock.fast_forward("01:30")
    expect(page.locator("#idle-warning-overlay")).to_be_hidden()


def test_full_idle_triggers_logout_and_clears_session_storage(page: Page, live_server_url: str):
    _login(page, live_server_url)
    page.clock.fast_forward("10:00")
    expect(page.locator("#login-screen")).to_be_visible()
    expect(page.locator("#dashboard-screen")).to_be_hidden()
    assert page.evaluate("() => sessionStorage.length") == 0


def test_manual_logout_still_works(page: Page, live_server_url: str):
    _login(page, live_server_url)
    page.get_by_role("button", name="admin", exact=False).click()
    page.get_by_role("menuitem", name="Wyloguj").click()
    expect(page.locator("#login-screen")).to_be_visible()
    assert page.evaluate("() => sessionStorage.length") == 0
