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
    page.get_by_role("button", name="Treemapa portfela").click()


def test_admin_can_open_treemap_and_see_positions_rendered_with_pl_deltas(
    page: Page, live_server_url: str
):
    _login(page, live_server_url)
    _open_treemap(page)

    expect(page.get_by_role("heading", name="Portfel główny")).to_be_visible()
    expect(page.get_by_role("heading", name="IKZE")).to_be_visible()

    main_container = page.locator("#treemap-main")
    expect(main_container.locator(".treemap-cell.positive")).to_contain_text("PKO")
    expect(main_container.locator(".treemap-cell.positive")).to_contain_text("+200")
    expect(main_container.locator(".treemap-cell.negative")).to_contain_text("CDR")
    expect(main_container.locator(".treemap-cell.negative")).to_contain_text("-100")
    expect(main_container.locator(".treemap-cell.no-data")).to_contain_text("NEW")
    expect(main_container.locator(".treemap-cell.no-data")).to_contain_text("brak danych")

    expect(main_container).to_contain_text("D/D:")
    expect(main_container).to_contain_text("Total:")
    expect(main_container).to_contain_text("Zakup:")
    expect(main_container.locator(".treemap-cell.no-data")).to_contain_text("Zakup: brak danych")

    ikze_container = page.locator("#treemap-ikze")
    expect(ikze_container.locator(".treemap-cell", has_text="ALE")).to_be_visible()
    expect(ikze_container.locator(".treemap-cell", has_text="KGH")).to_be_visible()


def test_resizing_window_reflows_treemap_layout_without_reopening_view(
    page: Page, live_server_url: str
):
    page.set_viewport_size({"width": 1280, "height": 800})
    _login(page, live_server_url)
    _open_treemap(page)

    cell = page.locator("#treemap-main .treemap-cell").first
    expect(cell).to_be_visible()
    original_style = cell.get_attribute("style")

    page.set_viewport_size({"width": 700, "height": 800})

    expect(page.locator(".treemap-wallets")).to_have_css("flex-direction", "column")
    expect(cell).not_to_have_attribute("style", original_style)


def test_user_role_has_no_treemap_menu_item_or_dom_node(page: Page, live_server_url: str):
    _login(page, live_server_url, key=_USER_KEY)
    page.get_by_role("button", name="Użytkownik").click()

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


def test_opening_treemap_sets_view_url_param(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_treemap(page)

    expect(page).to_have_url(re.compile(r"\?view=treemap"))


def test_hovering_treemap_cell_shows_outline(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_treemap(page)

    cell = page.locator("#treemap-main .treemap-cell").first
    cell.hover()
    expect(cell).to_have_css("outline-style", "solid")


def test_clicking_treemap_cell_opens_popup_with_summary(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_treemap(page)

    page.locator("#treemap-main .treemap-cell", has_text="PKO").first.click()

    popup = page.locator("#treemap-popup-backdrop")
    expect(popup).to_be_visible()
    expect(page.locator("#tc-popup-ticker")).to_have_text("PKO")
    expect(page.locator("#tc-popup-daily")).to_contain_text("D/D:")
    expect(page.locator("#tc-popup-total")).to_contain_text("Total:")
    expect(page.locator("#tc-popup-since")).to_contain_text("Zakup:")


def test_clicking_popup_button_navigates_to_filtered_announcements(
    page: Page, live_server_url: str
):
    _login(page, live_server_url)
    _open_treemap(page)

    page.locator("#treemap-main .treemap-cell", has_text="PKO").first.click()
    expect(page.locator("#treemap-popup-backdrop")).to_be_visible()

    # The treemap's "ticker" field is actually a company display name (e.g. "Toya"
    # from XTB OCR), so navigation must use the announcements `company` filter
    # (partial match) rather than its exact-match `ticker` filter.
    with page.expect_response(re.compile(r"/announcements\?.*company=PKO")):
        page.get_by_role("button", name="Ostatnie podsumowania").click()

    expect(page.locator("#announcements-view")).to_be_visible()
    expect(page.locator("#treemap-view")).to_be_hidden()
    expect(page.locator("#f-company")).to_have_value("PKO")


def test_closing_popup_does_not_navigate(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_treemap(page)

    requests: list[str] = []
    page.on("request", lambda r: requests.append(r.url))

    page.locator("#treemap-main .treemap-cell", has_text="PKO").first.click()
    popup = page.locator("#treemap-popup-backdrop")
    expect(popup).to_be_visible()
    url_before_close = page.url

    page.locator("#tc-popup-close").click()
    expect(popup).to_be_hidden()
    expect(page.locator("#announcements-view")).to_be_hidden()
    assert not any("/announcements" in url for url in requests)
    assert page.url == url_before_close


def test_clicking_treemap_cell_preserves_other_active_filters(
    page: Page, live_server_url: str
):
    _login(page, live_server_url)
    page.locator("#f-ticker").fill("ABC")
    _open_treemap(page)

    page.locator("#treemap-main .treemap-cell", has_text="PKO").first.click()
    with page.expect_response(re.compile(r"/announcements")):
        page.get_by_role("button", name="Ostatnie podsumowania").click()

    expect(page.locator("#f-ticker")).to_have_value("ABC")
    expect(page.locator("#f-company")).to_have_value("PKO")
