from playwright.sync_api import Page, expect

_ADMIN_KEY = "e2e-admin-key"


def test_login_screen_has_brand_and_hint(page: Page, live_server_url: str):
    page.goto(live_server_url)
    expect(page.locator(".login-banner")).to_be_visible()
    expect(page.locator(".login-brand")).to_be_visible()
    expect(page.locator(".login-brand p")).to_be_visible()
    expect(page.locator(".login-hint")).to_be_visible()
    expect(page.locator(".login-hint")).to_contain_text("Klucz API")


def test_wrong_api_key_shows_error(page: Page, live_server_url: str):
    page.goto(live_server_url)
    page.get_by_label("Klucz API").fill("not-a-real-key")
    page.get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#login-error")).to_be_visible()
