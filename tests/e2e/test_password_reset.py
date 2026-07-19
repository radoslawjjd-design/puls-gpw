"""E2E tests — password reset flow from the login screen (PUL-85).

Risk: the #/reset-hasla view must let a user request a reset link, show the
SAME confirmation for existing and unknown accounts (anti-enumeration at the
UI level), validate e-mail syntax client-side, and route back to login.

Seed: tests/e2e/test_landing_auth.py (auth-hash routing patterns).
"""
import re

from playwright.sync_api import Page, expect

from tests.e2e.conftest import e2e_unique_email


def _open_reset_view(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.locator(".landing-nav").get_by_role("button", name="Zaloguj się").click()
    page.get_by_role("button", name="Nie pamiętasz hasła?").click()
    expect(page.locator("#reset-form")).to_be_visible()
    expect(page).to_have_url(re.compile(r"#/reset-hasla"))


def test_reset_happy_path_shows_confirmation_with_resend_and_back(
    page: Page, live_server_url: str
):
    _open_reset_view(page, live_server_url)

    page.locator("#reset-form").get_by_label("E-mail").fill(e2e_unique_email())
    page.get_by_role("button", name="Wyślij link").click()

    confirmation = page.locator("#reset-confirmation")
    expect(confirmation).to_be_visible()
    expect(confirmation).to_contain_text("Jeśli konto istnieje")
    expect(confirmation.get_by_role("button", name="Wyślij ponownie")).to_be_visible()
    expect(confirmation.get_by_role("button", name="Wróć do logowania")).to_be_visible()
    expect(page.locator("#reset-form")).to_be_hidden()


def test_reset_unknown_email_shows_identical_confirmation(page: Page, live_server_url: str):
    """Anti-enumeration: an address that never registered gets the exact same
    confirmation state as an existing one."""
    _open_reset_view(page, live_server_url)

    page.locator("#reset-form").get_by_label("E-mail").fill("nigdy-nie-istnialo@example.com")
    page.get_by_role("button", name="Wyślij link").click()

    confirmation = page.locator("#reset-confirmation")
    expect(confirmation).to_be_visible()
    expect(confirmation).to_contain_text("Jeśli konto istnieje")


def test_reset_invalid_email_shows_inline_error_without_request(
    page: Page, live_server_url: str
):
    _open_reset_view(page, live_server_url)

    requests_made = []
    page.on(
        "request",
        lambda r: requests_made.append(r.url) if "reset-password" in r.url else None,
    )
    page.locator("#reset-form").get_by_label("E-mail").fill("to-nie-jest-email")
    page.get_by_role("button", name="Wyślij link").click()

    expect(page.locator("#reset-email-error")).to_have_text("Podaj poprawny adres e-mail.")
    expect(page.locator("#reset-form")).to_be_visible()
    assert requests_made == []


def test_reset_back_to_login_restores_login_form(page: Page, live_server_url: str):
    _open_reset_view(page, live_server_url)

    page.get_by_role("button", name="Wróć do logowania").click()

    expect(page.locator("#email-login-form")).to_be_visible()
    expect(page.locator("#reset-form")).to_be_hidden()
    expect(page).to_have_url(re.compile(r"#/logowanie"))


def test_reset_deep_link_renders_reset_view_directly(page: Page, live_server_url: str):
    """Hash routing regression: #/reset-hasla as the entry URL must land on the
    reset form, not the landing page."""
    page.goto(f"{live_server_url}/#/reset-hasla")

    expect(page.locator("#reset-form")).to_be_visible()
    expect(page.locator("#landing-view")).to_be_hidden()
