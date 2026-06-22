import re

from playwright.sync_api import Page, expect

_ADMIN_KEY = "e2e-admin-key"
_USER_KEY = "e2e-user-key"


def _login(page: Page, base_url: str, key: str = _ADMIN_KEY) -> None:
    page.goto(base_url)
    page.get_by_label("Klucz API").fill(key)
    page.get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")


def _open_x_history(page: Page) -> None:
    page.get_by_role("button", name="admin").click()
    page.get_by_role("menuitem", name="Historia postów X").click()


def test_menu_shows_x_history_above_wyloguj_for_admin(page: Page, live_server_url: str):
    _login(page, live_server_url)
    page.get_by_role("button", name="admin").click()
    menu_items = page.get_by_role("menuitem")
    expect(menu_items).to_have_count(3)
    expect(menu_items.nth(0)).to_have_text("Historia postów X")
    expect(menu_items.nth(1)).to_have_text("Treemapa portfela")
    expect(menu_items.nth(2)).to_have_text("Wyloguj")


def test_clicking_menu_item_renders_x_posts_table(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_x_history(page)
    expect(page.get_by_role("cell", name="post-pub-1")).to_be_visible()
    expect(page.get_by_role("cell", name="post-partial-1")).to_be_visible()


def test_opening_x_history_sets_view_url_param(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_x_history(page)

    expect(page).to_have_url(re.compile(r"view=x-history"))


def test_paging_to_page_2_changes_url(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_x_history(page)

    page.get_by_role("button", name=re.compile("Następna")).click()
    expect(page.locator("#xp-page-label")).to_have_text("Strona 2")
    expect(page).to_have_url(re.compile(r"view=x-history&page=2"))


def test_clicking_topbar_heading_returns_to_announcements(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_x_history(page)
    expect(page.get_by_role("cell", name="post-pub-1")).to_be_visible()

    page.get_by_role("heading", name="puls-gpw").click()

    expect(page.locator("#page-label")).to_be_visible()
    expect(page.get_by_role("cell", name="post-pub-1")).not_to_be_visible()


def test_filter_by_window_narrows_results(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_x_history(page)
    expect(page.get_by_role("cell", name="post-pub-1")).to_be_visible()

    page.get_by_role("combobox", name="Okno").select_option("ranek")
    page.get_by_role("button", name="Filtruj").click()

    expect(page.get_by_role("cell", name="post-pub-1")).to_be_visible()
    expect(page.get_by_role("cell", name="post-partial-1")).not_to_be_visible()


def test_filter_by_status_narrows_results(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_x_history(page)
    expect(page.get_by_role("cell", name="post-pub-1")).to_be_visible()

    page.get_by_role("combobox", name="Status").select_option("failed")
    page.get_by_role("button", name="Filtruj").click()

    expect(page.get_by_role("cell", name="post-pub-1")).not_to_be_visible()
    expect(page.get_by_role("cell", name="post-partial-1")).not_to_be_visible()
    expect(page.get_by_role("row", name=re.compile("Nieudany"))).to_be_visible()


def test_clicking_published_row_opens_modal_with_numbered_tweets_and_link(
    page: Page, live_server_url: str
):
    _login(page, live_server_url)
    _open_x_history(page)

    page.get_by_role("cell", name="post-pub-1").click()

    expect(page.get_by_role("dialog")).to_be_visible()
    expect(page.get_by_text("Tweet 1/2", exact=True)).to_be_visible()
    expect(page.get_by_text("Tweet 2/2", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name=re.compile("zobacz na X")).first).to_have_attribute(
        "href", "https://x.com/i/web/status/1111111111"
    )


def test_clicking_failed_row_shows_brak_treci_fallback(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_x_history(page)

    page.get_by_role("row", name=re.compile("Nieudany")).click()

    expect(page.get_by_role("dialog")).to_be_visible()
    expect(page.get_by_text("Brak treści")).to_be_visible()


def test_user_role_has_no_x_history_menu_item_or_dom_node(page: Page, live_server_url: str):
    _login(page, live_server_url, key=_USER_KEY)
    page.get_by_role("button", name="user").click()

    expect(page.get_by_role("menuitem", name="Historia postów X")).not_to_be_attached()
    expect(page.locator("#x-history-btn")).not_to_be_attached()
    expect(page.locator("#x-history-view")).not_to_be_attached()


def test_user_role_never_triggers_x_posts_network_request(page: Page, live_server_url: str):
    requests: list[str] = []
    page.on("request", lambda r: requests.append(r.url))

    _login(page, live_server_url, key=_USER_KEY)
    with page.expect_response(re.compile(r"/announcements")):
        page.get_by_role("button", name="Filtruj").click()

    assert not any("/admin/x-posts" in url for url in requests)
