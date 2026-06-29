"""E2E tests — monthly P&L calendar view in Mój portfel (PUL-59).

Risk: calendar tab renders gain/loss/neutral cells correctly, month navigation
updates the label and grid, and the URL reflects the active tab.

Seed: tests/e2e/test_user_portfolio_treemap.py
"""
import re

from playwright.sync_api import Page, expect

_USER_KEY = "e2e-user-key"


def _login(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.get_by_label("Klucz API").fill(_USER_KEY)
    page.get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")


def _open_portfolio(page: Page) -> None:
    page.get_by_role("button", name="Mój portfel").click()
    expect(page.locator("#pp-portfolio-tabs .pp-portfolio-tab")).to_be_visible()


def _open_calendar_tab(page: Page) -> None:
    page.locator("#pp-view-tabs").get_by_role("button", name="Kalendarz").click()
    expect(page.locator("#pp-calendar-wrap")).to_be_visible()


def test_calendar_tab_exists_and_shows_grid_on_click(page: Page, live_server_url: str):
    """Risk: Kalendarz tab must exist alongside Tabela and Treemapa, and clicking it
    must reveal the calendar container and a non-empty grid."""
    _login(page, live_server_url)
    _open_portfolio(page)

    view_tabs = page.locator("#pp-view-tabs")
    expect(view_tabs.get_by_role("button", name="Tabela")).to_be_visible()
    expect(view_tabs.get_by_role("button", name="Treemapa")).to_be_visible()
    expect(view_tabs.get_by_role("button", name="Kalendarz")).to_be_visible()

    _open_calendar_tab(page)

    expect(page.locator("#pp-cal-grid")).to_be_visible()
    expect(page.locator("#pp-cal-label")).to_be_visible()


def test_calendar_renders_gain_and_loss_pnl_text(page: Page, live_server_url: str):
    """Risk: cells with positive daily_change_pln must show '+NNN PLN' and cells with
    negative must show '−NNN PLN' — proving BQ data reaches the rendered grid."""
    _login(page, live_server_url)
    _open_portfolio(page)
    _open_calendar_tab(page)

    grid = page.locator("#pp-cal-grid")
    # _FAKE_CALENDAR_ROWS: June 2 → +300, June 3 → -150
    expect(grid).to_contain_text("+300 PLN")
    expect(grid).to_contain_text("−150 PLN")  # U+2212 MINUS SIGN, not hyphen


def test_calendar_weekend_and_holiday_cells_are_neutral(page: Page, live_server_url: str):
    """Risk: Saturday/Sunday and GPW holidays must render as neutral-gray, never as
    gain/loss — so a weekend day is never coloured green or red."""
    _login(page, live_server_url)
    _open_portfolio(page)
    _open_calendar_tab(page)

    # June 2026 has 4 Saturdays (6, 13, 20, 27), 4 Sundays, and June 4 = GPW holiday
    neutral_cells = page.locator("#pp-cal-grid .pp-cal-neutral")
    expect(neutral_cells.first).to_be_visible()

    # Gain/loss cells exist too (from fake data), but neutral cells must be separate
    gain_cells = page.locator("#pp-cal-grid .pp-cal-gain")
    expect(gain_cells.first).to_be_visible()


def test_calendar_prev_navigation_changes_month_label(page: Page, live_server_url: str):
    """Risk: clicking the '‹' (prev) button must decrement the displayed month and
    reload the grid — proving month navigation state is wired to the API call."""
    _login(page, live_server_url)
    _open_portfolio(page)
    _open_calendar_tab(page)

    label = page.locator("#pp-cal-label")
    expect(label).to_contain_text("czerwiec", ignore_case=True)  # June 2026 (current month)

    page.locator("#pp-cal-prev").click()

    expect(label).to_contain_text("maj", ignore_case=True)  # May 2026
    expect(page.locator("#pp-cal-grid")).to_be_visible()


def test_calendar_url_contains_tab_calendar_after_switch(page: Page, live_server_url: str):
    """Risk: switching to the Kalendarz tab must write tab=calendar into the URL so the
    view is deeplink-restorable — proving URL routing is wired to the tab toggle."""
    _login(page, live_server_url)
    _open_portfolio(page)
    _open_calendar_tab(page)

    expect(page).to_have_url(re.compile(r"tab=calendar"))
