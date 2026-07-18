from playwright.sync_api import Page, expect

_ADMIN_KEY = "e2e-admin-key"


def _open_api_key_panel(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.locator(".landing-nav").get_by_role("button", name="Zaloguj się").click()
    page.get_by_role("button", name="Mam klucz API").click()


def test_landing_has_hero_features_and_cards(page: Page, live_server_url: str):
    page.goto(live_server_url)
    expect(page.locator(".landing-hero")).to_be_visible()
    expect(page.locator(".landing-hero h1")).to_contain_text("Analizy komunikatów giełdowych")
    expect(page.locator(".landing-feature")).to_have_count(3)
    # Cards from the public endpoint mock render without score or sentiment.
    expect(page.locator(".lc-card")).to_have_count(3)
    expect(page.locator(".lc-card").first).to_contain_text("PKO")
    expect(page.locator("#landing-cards")).not_to_contain_text("pozytywny")
    expect(page.locator("#landing-cards")).not_to_contain_text("neutralny")


def test_nav_buttons_open_login_and_register_views(page: Page, live_server_url: str):
    page.goto(live_server_url)
    page.locator(".landing-nav").get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#email-login-form")).to_be_visible()
    expect(page.locator(".landing-hero")).to_be_hidden()
    page.get_by_role("button", name="Wróć na stronę główną").click()
    expect(page.locator(".landing-hero")).to_be_visible()
    page.locator(".landing-nav").get_by_role("button", name="Załóż konto").click()
    expect(page.locator("#register-form")).to_be_visible()
    expect(page.get_by_label("Powtórz hasło")).to_be_visible()
    expect(page.locator("#register-form .login-hint")).to_contain_text("litera i jedna cyfra")


def test_logo_click_returns_to_landing(page: Page, live_server_url: str):
    page.goto(live_server_url)
    page.locator(".landing-nav").get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#email-login-form")).to_be_visible()
    page.locator(".landing-nav .landing-logo").click()
    expect(page.locator(".landing-hero")).to_be_visible()
    page.locator(".landing-nav").get_by_role("button", name="Załóż konto").click()
    expect(page.locator("#register-form")).to_be_visible()
    page.locator(".landing-nav .landing-nav-name").click()
    expect(page.locator(".landing-hero")).to_be_visible()


def test_swap_links_switch_between_forms(page: Page, live_server_url: str):
    page.goto(live_server_url)
    page.locator(".landing-nav").get_by_role("button", name="Zaloguj się").click()
    page.get_by_role("button", name="Nie masz konta? Załóż je").click()
    expect(page.locator("#register-form")).to_be_visible()
    page.get_by_role("button", name="Masz już konto? Zaloguj się").click()
    expect(page.locator("#email-login-form")).to_be_visible()


def test_api_key_path_is_behind_link(page: Page, live_server_url: str):
    page.goto(live_server_url)
    expect(page.get_by_label("Klucz API")).to_be_hidden()
    page.locator(".landing-nav").get_by_role("button", name="Zaloguj się").click()
    page.get_by_role("button", name="Mam klucz API").click()
    expect(page.get_by_label("Klucz API")).to_be_visible()
    expect(page.locator("#email-auth-panel")).to_be_hidden()
    page.get_by_role("button", name="Wróć do logowania e-mail").click()
    expect(page.get_by_label("Klucz API")).to_be_hidden()
    expect(page.locator("#email-login-form")).to_be_visible()


def test_wrong_api_key_shows_error(page: Page, live_server_url: str):
    _open_api_key_panel(page, live_server_url)
    page.get_by_label("Klucz API").fill("not-a-real-key")
    page.locator("#api-key-panel").get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#login-error")).to_be_visible()


def test_api_key_login_reaches_dashboard(page: Page, live_server_url: str):
    _open_api_key_panel(page, live_server_url)
    page.get_by_label("Klucz API").fill(_ADMIN_KEY)
    page.locator("#api-key-panel").get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")
