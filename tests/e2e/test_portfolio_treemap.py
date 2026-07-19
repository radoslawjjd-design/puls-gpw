import re

from playwright.sync_api import Page, expect

from tests.e2e.conftest import e2e_login_email



def _login(page: Page, base_url: str) -> None:
    # PUL-74: widoki per-user są JWT-only — logowanie przez formularz e-mail.
    e2e_login_email(page, base_url)


def test_user_role_has_no_treemap_menu_item_or_dom_node(page: Page, live_server_url: str):
    """The old admin-only XTB treemap button and view must never appear for user role."""
    _login(page, live_server_url)

    expect(page.locator("#treemap-btn")).not_to_be_attached()
    expect(page.locator("#treemap-view")).not_to_be_attached()


def test_user_role_never_triggers_treemap_network_request(page: Page, live_server_url: str):
    """User role must not issue a request to the admin-only /admin/portfolio/treemap endpoint."""
    requests: list[str] = []
    page.on("request", lambda r: requests.append(r.url))

    _login(page, live_server_url)
    with page.expect_response(re.compile(r"/announcements")):
        page.get_by_role("button", name="Filtruj").click()

    assert not any("/admin/portfolio/treemap" in url for url in requests)
