import re

from playwright.sync_api import Page, expect

_USER_KEY = "e2e-user-key"


def _login(page: Page, base_url: str, key: str = _USER_KEY) -> None:
    page.goto(base_url)
    page.get_by_label("Klucz API").fill(key)
    page.get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")


def test_user_role_has_no_treemap_menu_item_or_dom_node(page: Page, live_server_url: str):
    """The old admin-only XTB treemap button and view must never appear for user role."""
    _login(page, live_server_url, key=_USER_KEY)

    expect(page.locator("#treemap-btn")).not_to_be_attached()
    expect(page.locator("#treemap-view")).not_to_be_attached()


def test_user_role_never_triggers_treemap_network_request(page: Page, live_server_url: str):
    """User role must not issue a request to the admin-only /admin/portfolio/treemap endpoint."""
    requests: list[str] = []
    page.on("request", lambda r: requests.append(r.url))

    _login(page, live_server_url, key=_USER_KEY)
    with page.expect_response(re.compile(r"/announcements")):
        page.get_by_role("button", name="Filtruj").click()

    assert not any("/admin/portfolio/treemap" in url for url in requests)
