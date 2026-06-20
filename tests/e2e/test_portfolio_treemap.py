import re

from playwright.sync_api import Page, expect

_ADMIN_KEY = "e2e-admin-key"
_USER_KEY = "e2e-user-key"


def _login(page: Page, base_url: str, key: str = _ADMIN_KEY) -> None:
    page.goto(base_url)
    page.get_by_label("Klucz API").fill(key)
    page.get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")


def _open_treemap(page: Page) -> None:
    page.get_by_role("button", name="admin").click()
    page.get_by_role("menuitem", name="Treemapa portfela").click()


def test_admin_can_open_treemap_and_see_positions_rendered_with_pl_deltas(
    page: Page, live_server_url: str
):
    _login(page, live_server_url)
    _open_treemap(page)

    container = page.locator("#treemap-container")
    expect(container.locator(".treemap-cell.positive")).to_contain_text("PKO")
    expect(container.locator(".treemap-cell.positive")).to_contain_text("+200")
    expect(container.locator(".treemap-cell.negative")).to_contain_text("CDR")
    expect(container.locator(".treemap-cell.negative")).to_contain_text("-100")
    expect(container.locator(".treemap-cell.no-data")).to_contain_text("NEW")
    expect(container.locator(".treemap-cell.no-data")).to_contain_text("brak danych")


def test_user_role_has_no_treemap_menu_item_or_dom_node(page: Page, live_server_url: str):
    _login(page, live_server_url, key=_USER_KEY)
    page.get_by_role("button", name="user").click()

    expect(page.get_by_role("menuitem", name="Treemapa portfela")).not_to_be_attached()
    expect(page.locator("#treemap-btn")).not_to_be_attached()
    expect(page.locator("#treemap-view")).not_to_be_attached()


def test_user_role_never_triggers_treemap_network_request(page: Page, live_server_url: str):
    requests: list[str] = []
    page.on("request", lambda r: requests.append(r.url))

    _login(page, live_server_url, key=_USER_KEY)
    with page.expect_response(re.compile(r"/announcements")):
        page.get_by_role("button", name="Filtruj").click()

    assert not any("/admin/portfolio/treemap" in url for url in requests)
