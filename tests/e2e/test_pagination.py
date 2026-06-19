import re

from playwright.sync_api import Page, expect

_ADMIN_KEY = "e2e-admin-key"


def _login(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.get_by_label("Klucz API").fill(_ADMIN_KEY)
    page.get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")


def test_initial_page_shows_page_1(page: Page, live_server_url: str):
    _login(page, live_server_url)
    expect(page.locator("#page-label")).to_have_text("Strona 1")
    expect(page.get_by_role("button", name=re.compile("Poprzednia"))).to_be_disabled()


def test_next_advances_page(page: Page, live_server_url: str):
    _login(page, live_server_url)
    page.get_by_role("button", name=re.compile("Następna")).click()
    expect(page.locator("#page-label")).to_have_text("Strona 2")
    expect(page.get_by_role("button", name=re.compile("Poprzednia"))).to_be_enabled()


def test_filter_resets_page(page: Page, live_server_url: str):
    _login(page, live_server_url)
    page.get_by_role("button", name=re.compile("Następna")).click()
    expect(page.locator("#page-label")).to_have_text("Strona 2")
    page.get_by_role("button", name="Filtruj").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")


def test_page_size_resets_page(page: Page, live_server_url: str):
    _login(page, live_server_url)
    page.get_by_role("button", name=re.compile("Następna")).click()
    expect(page.locator("#page-label")).to_have_text("Strona 2")
    page.get_by_role("combobox", name="Rozmiar strony").select_option("50")
    expect(page.locator("#page-label")).to_have_text("Strona 1")
