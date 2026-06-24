from playwright.sync_api import Page, expect

_ADMIN_KEY = "e2e-admin-key"


def _login(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.get_by_label("Klucz API").fill(_ADMIN_KEY)
    page.get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")


def test_trigger_click_opens_menu_and_escape_closes_with_focus_management(
    page: Page, live_server_url: str
):
    _login(page, live_server_url)
    trigger = page.get_by_role("button", name="admin")
    menu = page.get_by_role("menu")

    expect(menu).to_be_hidden()
    expect(trigger).to_have_attribute("aria-expanded", "false")

    trigger.click()
    expect(menu).to_be_visible()
    expect(trigger).to_have_attribute("aria-expanded", "true")
    expect(page.get_by_role("menuitem", name="Wyloguj")).to_be_focused()

    page.keyboard.press("Escape")
    expect(menu).to_be_hidden()
    expect(trigger).to_have_attribute("aria-expanded", "false")
    expect(trigger).to_be_focused()


def test_clicking_outside_closes_menu_without_the_opening_click_closing_it(
    page: Page, live_server_url: str
):
    _login(page, live_server_url)
    trigger = page.get_by_role("button", name="admin")
    menu = page.get_by_role("menu")

    trigger.click()
    # The same click that opens the menu must not be swallowed by the
    # outside-click-closes handler — it has to stay open right after opening.
    expect(menu).to_be_visible()

    page.get_by_role("heading", name="Faro").click()
    expect(menu).to_be_hidden()
