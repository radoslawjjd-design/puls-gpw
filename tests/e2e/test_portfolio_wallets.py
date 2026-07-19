"""E2E tests — portfolio wallet management (PUL-64 Phase 6)."""
import json
import re

from playwright.sync_api import Page, expect

from tests.e2e.conftest import _FAKE_PORTFOLIO_ID, e2e_login_email



def _login(page: Page, base_url: str) -> None:
    # PUL-74: widoki per-user są JWT-only — logowanie przez formularz e-mail.
    e2e_login_email(page, base_url)


def _open_portfolio_positions(page: Page) -> None:
    page.get_by_role("button", name="Mój portfel").click()
    expect(page.locator("#pp-portfolio-tabs .pp-portfolio-tab")).to_be_visible()


def test_portfolio_tabs_show_after_login(page: Page, live_server_url: str):
    """Risk: opening 'Mój portfel' loads wallet tabs from API and renders the active one."""
    _login(page, live_server_url)
    _open_portfolio_positions(page)

    expect(page.locator("#pp-portfolio-tabs")).to_contain_text("Główny")
    expect(page.locator(".pp-portfolio-tab.active")).to_be_visible()


def test_add_portfolio_modal_opens(page: Page, live_server_url: str):
    """Risk: 'Dodaj portfel' button opens modal with a portfolio-type select."""
    _login(page, live_server_url)
    _open_portfolio_positions(page)

    page.locator("#pp-add-portfolio-btn").click()

    expect(page.locator("#pp-add-portfolio-overlay")).to_be_visible()
    expect(page.locator("#pp-portfolio-type-select")).to_be_visible()
    expect(page.locator("#pp-portfolio-type-select option[value='glowny']")).to_be_attached()
    expect(page.locator("#pp-portfolio-type-select option[value='ikze']")).to_be_attached()
    expect(page.locator("#pp-portfolio-type-select option[value='inny']")).to_be_attached()


def test_add_portfolio_creates_tab(page: Page, live_server_url: str):
    """Risk: submitting Dodaj portfel form POSTs to API and closes modal on success."""
    _login(page, live_server_url)
    _open_portfolio_positions(page)

    page.locator("#pp-add-portfolio-btn").click()
    expect(page.locator("#pp-add-portfolio-overlay")).to_be_visible()

    page.locator("#pp-portfolio-type-select").select_option("ikze")

    with page.expect_response(re.compile(r"/api/portfolio/wallets")):
        page.locator("#pp-portfolio-modal-save").click()

    # Modal must close after a successful 201 response
    expect(page.locator("#pp-add-portfolio-overlay")).to_be_hidden()
    # Tabs area is still visible (portfolios reloaded)
    expect(page.locator("#pp-portfolio-tabs")).to_be_visible()


def test_positions_table_scoped_to_active_tab(page: Page, live_server_url: str):
    """Risk: active portfolio tab gates the positions fetch — positions from other
    wallets must not bleed in."""
    _login(page, live_server_url)
    _open_portfolio_positions(page)

    # PKO and CDR come from _FAKE_PORTFOLIO_POSITIONS scoped to _FAKE_PORTFOLIO_ID
    expect(page.locator("#pp-tbody")).to_contain_text("PKO")
    expect(page.locator("#pp-tbody")).to_contain_text("CDR")


def test_add_position_sends_portfolio_id(page: Page, live_server_url: str):
    """Risk: POST /api/portfolio/positions must include portfolio_id in the request body."""
    _login(page, live_server_url)
    _open_portfolio_positions(page)

    portfolio_id_sent: list[str] = []

    def capture_request(request):
        if "/api/portfolio/positions" in request.url and request.method == "POST":
            try:
                body = json.loads(request.post_data or "{}")
                if "portfolio_id" in body:
                    portfolio_id_sent.append(body["portfolio_id"])
            except (json.JSONDecodeError, Exception):
                pass

    page.on("request", capture_request)

    pp = page.locator("#portfolio-positions-view")
    pp.get_by_role("button", name="Dodaj pozycję").click()
    pp.get_by_placeholder("Ticker (np. PKO)").fill("XTB")
    pp.get_by_placeholder("Nazwa spółki").fill("XTB SA")
    pp.get_by_placeholder("Ilość akcji").fill("5")
    pp.get_by_placeholder("Śr. cena zakupu (PLN)").fill("100")

    with page.expect_response(re.compile(r"/api/portfolio/positions")):
        pp.get_by_role("button", name="Dodaj", exact=True).click()

    assert portfolio_id_sent, "POST /api/portfolio/positions did not include portfolio_id"
    assert portfolio_id_sent[0] == _FAKE_PORTFOLIO_ID
