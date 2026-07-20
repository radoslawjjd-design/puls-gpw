"""E2E — e-mail verification at registration (PUL-86 Phase 3).

Fake backend (conftest): e-mail z markerem "unverified" → bramka 403;
marker "taken" → 409 z rejestracji; resend zawsze 204 (anty-enumeracja).
"""
from playwright.sync_api import Page, expect

from tests.e2e.conftest import E2E_PASSWORD, e2e_login_email, e2e_unique_email

_RESEND_OK_SNIPPET = "wysłaliśmy nowy link"


def _submit_register(page: Page, base_url: str, email: str) -> None:
    page.goto(base_url)
    page.locator(".landing-nav").get_by_role("button", name="Załóż konto").click()
    form = page.locator("#register-form")
    expect(form).to_be_visible()
    form.get_by_label("E-mail").fill(email)
    form.get_by_label("Hasło", exact=True).fill(E2E_PASSWORD)
    form.get_by_label("Powtórz hasło").fill(E2E_PASSWORD)
    form.get_by_role("button", name="Załóż konto").click()


def test_register_confirmation_resend_gives_feedback_and_reenables(
    page: Page, live_server_url: str
):
    _submit_register(page, live_server_url, e2e_unique_email())
    confirmation = page.locator("#register-confirmation")
    expect(confirmation).to_be_visible()
    confirmation.get_by_role("button", name="Wyślij ponownie").click()
    expect(page.locator("#register-resend-ok")).to_contain_text(_RESEND_OK_SNIPPET)
    # Przycisk wraca do stanu aktywnego — kolejna próba możliwa od razu.
    expect(confirmation.get_by_role("button", name="Wyślij ponownie")).to_be_enabled()


def test_unverified_login_shows_message_and_resend_button(
    page: Page, live_server_url: str
):
    page.goto(live_server_url)
    page.locator(".landing-nav").get_by_role("button", name="Zaloguj się").click()
    form = page.locator("#email-login-form")
    expect(form).to_be_visible()
    form.get_by_label("E-mail").fill("unverified-" + e2e_unique_email())
    form.get_by_label("Hasło", exact=True).fill(E2E_PASSWORD)
    form.get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#email-login-error")).to_have_text(
        "Potwierdź adres e-mail, aby się zalogować"
    )
    resend = page.locator("#login-resend-btn")
    expect(resend).to_be_visible()
    resend.click()
    expect(page.locator("#login-resend-ok")).to_contain_text(_RESEND_OK_SNIPPET)
    # Bramka trzyma: żadnego dashboardu bez potwierdzonego e-maila.
    expect(page.locator("#page-label")).to_be_hidden()


def test_verified_login_still_lands_in_dashboard(page: Page, live_server_url: str):
    """Regresja: zweryfikowane konta (domyślny fake) logują się bez zmian."""
    e2e_login_email(page, live_server_url)
    expect(page.locator("#role-badge")).to_have_text("Użytkownik")


def test_register_409_shows_resend_hint(page: Page, live_server_url: str):
    """Martwy zaułek "konto istnieje, mail zgubiony" ma widoczną drogę wyjścia."""
    _submit_register(page, live_server_url, "taken-" + e2e_unique_email())
    expect(page.locator("#register-error")).to_have_text("Email jest już zarejestrowany")
    resend = page.locator("#reg-resend-btn")
    expect(resend).to_be_visible()
    resend.click()
    expect(page.locator("#reg-resend-ok")).to_contain_text(_RESEND_OK_SNIPPET)
