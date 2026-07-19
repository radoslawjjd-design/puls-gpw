"""E2E tests — per-user portfolio treemap (PUL-64 Phase 6)."""
import re

from playwright.sync_api import Page, expect

from tests.e2e.conftest import (
    E2E_ADMIN_EMAIL,
    E2E_PASSWORD,
    e2e_login_email,
    e2e_unique_email,
)


def _login(page: Page, base_url: str, admin: bool = False) -> None:
    # PUL-74: widoki per-user są JWT-only — logowanie przez formularz e-mail;
    # wariant admin używa stałego konta z rolą admin w fake'owym BQ.
    e2e_login_email(page, base_url, email=E2E_ADMIN_EMAIL if admin else None)


def _open_portfolio_positions(page: Page) -> None:
    page.get_by_role("button", name="Mój portfel").click()
    expect(page.locator("#pp-portfolio-tabs .pp-portfolio-tab")).to_be_visible()


def _open_treemap_tab(page: Page) -> None:
    page.locator("#pp-view-tabs").get_by_role("button", name="Treemapa").click()
    expect(page.locator("#pp-treemap-wrap")).to_be_visible()


def test_admin_nav_has_no_old_treemap_btn(page: Page, live_server_url: str):
    """Risk: Phase 5 removed the admin-only XTB treemap nav button — must not exist in DOM."""
    _login(page, live_server_url, admin=True)

    expect(page.locator("#treemap-btn")).not_to_be_attached()
    expect(page.locator("#treemap-view")).not_to_be_attached()


def test_treemap_tab_visible_for_user_role(page: Page, live_server_url: str):
    """Risk: 'Treemapa' toggle tab must exist inside portfolio-positions-view for user role."""
    _login(page, live_server_url)
    _open_portfolio_positions(page)

    view_tabs = page.locator("#pp-view-tabs")
    expect(view_tabs.get_by_role("button", name="Tabela")).to_be_visible()
    expect(view_tabs.get_by_role("button", name="Treemapa")).to_be_visible()


def test_treemap_renders_cells_for_priced_positions(page: Page, live_server_url: str):
    """Risk: PKO (current_price=50.0) must produce a treemap cell; CDR (no price) must not."""
    _login(page, live_server_url)
    _open_portfolio_positions(page)
    _open_treemap_tab(page)

    wallets = page.locator("#pp-treemap-wallets")
    expect(wallets.locator(".treemap-cell", has_text="PKO")).to_be_visible()
    expect(wallets.locator(".treemap-cell", has_text="CDR")).not_to_be_attached()


def test_no_price_notice_shows_unpriceable_tickers(page: Page, live_server_url: str):
    """Risk: CDR (current_price=None) must appear in the no-price notice after treemap loads."""
    _login(page, live_server_url)
    _open_portfolio_positions(page)
    _open_treemap_tab(page)

    notice = page.locator("#pp-treemap-no-price-notice")
    expect(notice).to_be_visible()
    expect(notice).to_contain_text("CDR")


def test_treemap_cell_popup_opens_on_click(page: Page, live_server_url: str):
    """Risk: clicking a PKO treemap cell opens the popup with ticker and financial data."""
    _login(page, live_server_url)
    _open_portfolio_positions(page)
    _open_treemap_tab(page)

    page.locator("#pp-treemap-wallets .treemap-cell", has_text="PKO").click()

    popup = page.locator("#treemap-popup-backdrop")
    expect(popup).to_be_visible()
    expect(page.locator("#tc-popup-ticker")).to_have_text("PKO")
    expect(page.locator("#tc-popup-daily")).to_contain_text("D/D:")
    expect(page.locator("#tc-popup-total")).to_contain_text("Total:")


def test_portfolio_positions_url_deeplink(page: Page, live_server_url: str):
    """Risk: ?view=portfolio-positions in the URL must restore the portfolio view after login."""
    page.goto(f"{live_server_url}/?view=portfolio-positions")
    page.locator(".landing-nav").get_by_role("button", name="Zaloguj się").click()
    form = page.locator("#email-login-form")
    expect(form).to_be_visible()
    form.get_by_label("E-mail").fill(e2e_unique_email())
    form.get_by_label("Hasło", exact=True).fill(E2E_PASSWORD)
    form.get_by_role("button", name="Zaloguj się").click()

    # _applyUrlState reads ?view=portfolio-positions and calls showPortfolioPositionsView()
    expect(page.locator("#portfolio-positions-view")).to_be_visible()
    expect(page.locator("#announcements-view")).to_be_hidden()
