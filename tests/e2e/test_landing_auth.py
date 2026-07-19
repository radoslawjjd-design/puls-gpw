import re
import time

from playwright.sync_api import Page, expect

from tests.e2e.conftest import E2E_ADMIN_EMAIL, E2E_WRONG_PASSWORD

_ADMIN_KEY = "e2e-admin-key"
_GOOD_PASSWORD = "DobreHaslo1"


def _unique_email() -> str:
    return f"e2e-{int(time.time() * 1000)}@example.com"


def _open_login_form(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.locator(".landing-nav").get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#email-login-form")).to_be_visible()


def _login_via_email(page: Page, base_url: str, email: str | None = None) -> None:
    _open_login_form(page, base_url)
    page.locator("#email-login-form").get_by_label("E-mail").fill(email or _unique_email())
    page.locator("#email-login-form").get_by_label("Hasło", exact=True).fill(_GOOD_PASSWORD)
    page.locator("#email-login-form").get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")


def test_landing_cards_render_without_score_or_sentiment(page: Page, live_server_url: str):
    page.goto(live_server_url)
    expect(page.locator(".lc-card")).to_have_count(3)
    cards = page.locator("#landing-cards")
    for forbidden in ("pozytywny", "negatywny", "neutralny", "Score"):
        expect(cards).not_to_contain_text(forbidden)


def test_register_lands_in_dashboard_without_relogin(page: Page, live_server_url: str):
    page.goto(live_server_url)
    page.locator(".landing-nav").get_by_role("button", name="Załóż konto").click()
    form = page.locator("#register-form")
    expect(form).to_be_visible()
    form.get_by_label("E-mail").fill(_unique_email())
    form.get_by_label("Hasło", exact=True).fill(_GOOD_PASSWORD)
    form.get_by_label("Powtórz hasło").fill(_GOOD_PASSWORD)
    form.get_by_role("button", name="Załóż konto").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")
    expect(page.locator("#role-badge")).to_have_text("Użytkownik")
    # Stary hash #/rejestracja nie może przetrwać do dashboardu (F3, review p2).
    # PUL-84: sesja JWT pisze URL-state, więc dashboard dokleja ?page=1&page_size=20
    # — asercja pilnuje zniknięcia hasha, nie dokładnego URL-a.
    expect(page).not_to_have_url(re.compile(r"#/"))


def test_register_password_mismatch_shows_inline_error(page: Page, live_server_url: str):
    page.goto(live_server_url)
    page.locator(".landing-nav").get_by_role("button", name="Załóż konto").click()
    form = page.locator("#register-form")
    form.get_by_label("E-mail").fill(_unique_email())
    form.get_by_label("Hasło", exact=True).fill(_GOOD_PASSWORD)
    form.get_by_label("Powtórz hasło").fill(_GOOD_PASSWORD + "x")
    form.get_by_role("button", name="Załóż konto").click()
    expect(page.locator("#reg-password2-error")).to_have_text("Hasła muszą być identyczne.")
    expect(form).to_be_visible()


def test_login_lands_in_dashboard(page: Page, live_server_url: str):
    _login_via_email(page, live_server_url)
    expect(page.locator("#role-badge")).to_have_text("Użytkownik")
    # PUL-84: URL-state działa też na JWT — hash auth znika, query params zostają.
    expect(page).not_to_have_url(re.compile(r"#/"))


def test_wrong_password_shows_backend_error_and_stays_on_landing(
    page: Page, live_server_url: str
):
    _open_login_form(page, live_server_url)
    form = page.locator("#email-login-form")
    form.get_by_label("E-mail").fill(_unique_email())
    form.get_by_label("Hasło", exact=True).fill(E2E_WRONG_PASSWORD)
    form.get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#email-login-error")).to_have_text("Nieprawidłowy email lub hasło")
    expect(form).to_be_visible()
    # Przycisk wraca do stanu aktywnego — kolejna próba możliwa od razu.
    expect(form.get_by_role("button", name="Zaloguj się")).to_be_enabled()


def test_session_survives_reload_via_boot_probe(page: Page, live_server_url: str):
    _login_via_email(page, live_server_url)
    page.reload()
    # Cookie sesji + flaga hasSession → probe /api/auth/me → dashboard bez logowania.
    expect(page.locator("#page-label")).to_have_text("Strona 1")
    expect(page.locator(".landing-hero")).to_be_hidden()


def test_logout_returns_to_landing_and_reload_stays_there(
    page: Page, live_server_url: str
):
    _login_via_email(page, live_server_url)
    page.get_by_role("button", name="Użytkownik").click()
    page.get_by_role("menuitem", name="Wyloguj").click()
    expect(page.locator(".landing-hero")).to_be_visible()
    page.reload()
    # hasSession zdjęte przy logout — reload zostaje na landingu, bez probe-loopa.
    expect(page.locator(".landing-hero")).to_be_visible()
    expect(page.locator("#page-label")).to_be_hidden()


def test_admin_email_login_gets_admin_dashboard_and_survives_reload(
    page: Page, live_server_url: str
):
    """PUL-83 full parity: an email admin sees the admin surface (Score column,
    admin-table), and the boot probe keeps the admin role across a reload."""
    _login_via_email(page, live_server_url, email=E2E_ADMIN_EMAIL)
    expect(page.locator("#role-badge")).to_have_text("Admin")
    expect(page.get_by_role("columnheader", name="Score")).to_be_visible()
    expect(page.locator("#data-table")).to_have_class(re.compile(r"\badmin-table\b"))

    page.reload()
    expect(page.locator("#page-label")).to_have_text("Strona 1")
    expect(page.locator("#role-badge")).to_have_text("Admin")
    expect(page.get_by_role("columnheader", name="Score")).to_be_visible()


def test_user_email_login_sees_no_admin_surface(page: Page, live_server_url: str):
    """Regression for the no-leak boundary: a plain email user gets no Score
    column, no admin-table styling, and no score/sentiment data attributes in
    the rendered rows (admin rows carry data-score/data-sc)."""
    _login_via_email(page, live_server_url)
    expect(page.locator("#role-badge")).to_have_text("Użytkownik")
    expect(page.get_by_role("columnheader", name="Score")).to_have_count(0)
    expect(page.locator("#data-table")).not_to_have_class(re.compile(r"\badmin-table\b"))
    expect(page.locator("#table-body [data-score]")).to_have_count(0)


def test_api_key_path_still_reaches_dashboard(page: Page, live_server_url: str):
    _open_login_form(page, live_server_url)
    page.get_by_role("button", name="Mam klucz API").click()
    page.get_by_label("Klucz API").fill(_ADMIN_KEY)
    page.locator("#api-key-panel").get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")
