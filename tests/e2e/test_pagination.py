"""E2E tests — the announcements pager.

PUL-77: the pager used to infer where the list ended from how many rows came
back. The fixture is 40 rows at 20 per page, which is exactly the case that
inference gets wrong — the last page is FULL, so "fewer rows than I asked for"
never fires. See test_the_last_full_page_does_not_offer_a_next_one.
"""
import re

from playwright.sync_api import Page, expect

_ADMIN_KEY = "e2e-admin-key"


def _login(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.locator(".landing-nav").get_by_role("button", name="Zaloguj się").click()
    page.get_by_role("button", name="Mam klucz API").click()
    page.get_by_label("Klucz API").fill(_ADMIN_KEY)
    page.locator("#api-key-panel").get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1 z 2")


def test_initial_page_shows_page_1_of_the_total(page: Page, live_server_url: str):
    _login(page, live_server_url)
    expect(page.locator("#page-label")).to_have_text("Strona 1 z 2")
    expect(page.get_by_role("button", name=re.compile("Poprzednia"))).to_be_disabled()


def test_next_advances_page(page: Page, live_server_url: str):
    _login(page, live_server_url)
    page.get_by_role("button", name=re.compile("Następna")).click()
    expect(page.locator("#page-label")).to_have_text("Strona 2 z 2")
    expect(page.get_by_role("button", name=re.compile("Poprzednia"))).to_be_enabled()


def test_the_last_full_page_does_not_offer_a_next_one(page: Page, live_server_url: str):
    """The bug X-Total-Count exists to kill.

    40 rows at 20 per page means page 2 comes back with a full 20 rows. The old
    rule — "Next is dead once a page returns fewer rows than requested" — cannot
    fire on a full page, so Next stayed enabled and clicking it landed the user
    on an empty table with no way to tell whether the data or the app had failed.
    Knowing the total makes the last page knowable instead of guessable.
    """
    _login(page, live_server_url)
    next_btn = page.get_by_role("button", name=re.compile("Następna"))

    expect(next_btn).to_be_enabled()
    next_btn.click()

    expect(page.locator("#page-label")).to_have_text("Strona 2 z 2")
    expect(page.locator("#table-body tr")).to_have_count(20)  # a FULL last page
    expect(next_btn).to_be_disabled()


def test_filter_resets_page(page: Page, live_server_url: str):
    _login(page, live_server_url)
    page.get_by_role("button", name=re.compile("Następna")).click()
    expect(page.locator("#page-label")).to_have_text("Strona 2 z 2")
    page.get_by_role("button", name="Filtruj").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1 z 2")


def test_page_size_resets_page_and_recounts_the_pages(page: Page, live_server_url: str):
    """The page count is derived, not stored — a bigger page must collapse it."""
    _login(page, live_server_url)
    page.get_by_role("button", name=re.compile("Następna")).click()
    expect(page.locator("#page-label")).to_have_text("Strona 2 z 2")

    page.get_by_role("combobox", name="Rozmiar strony").select_option("50")

    # 40 rows no longer need a second page.
    expect(page.locator("#page-label")).to_have_text("Strona 1 z 1")
    expect(page.get_by_role("button", name=re.compile("Następna"))).to_be_disabled()
