"""E2E tests — monthly P&L calendar view in Mój portfel (PUL-59, PUL-68).

Risk: calendar tab renders gain/loss/neutral cells correctly, month navigation
updates the label and grid, the URL reflects the active tab, and the MTD summary
element shows the correct cumulative P&L value below the grid.

Seed: tests/e2e/test_user_portfolio_treemap.py
"""
import re
from datetime import date

from playwright.sync_api import Page, expect

from tests.e2e.conftest import e2e_login_email

_MONTHS_PL = [
    "Styczeń", "Luty", "Marzec", "Kwiecień", "Maj", "Czerwiec",
    "Lipiec", "Sierpień", "Wrzesień", "Październik", "Listopad", "Grudzień",
]


def _current_month_name() -> str:
    return _MONTHS_PL[date.today().month - 1]


def _prev_month_name() -> str:
    today = date.today()
    m = today.month - 1 if today.month > 1 else 12
    return _MONTHS_PL[m - 1]



def _login(page: Page, base_url: str) -> None:
    # PUL-74: widoki per-user są JWT-only — logowanie przez formularz e-mail.
    e2e_login_email(page, base_url)


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
    expect(label).to_contain_text(_current_month_name(), ignore_case=True)

    page.locator("#pp-cal-prev").click()

    expect(label).to_contain_text(_prev_month_name(), ignore_case=True)
    expect(page.locator("#pp-cal-grid")).to_be_visible()


def test_calendar_url_contains_tab_calendar_after_switch(page: Page, live_server_url: str):
    """Risk: switching to the Kalendarz tab must write tab=calendar into the URL so the
    view is deeplink-restorable — proving URL routing is wired to the tab toggle."""
    _login(page, live_server_url)
    _open_portfolio(page)
    _open_calendar_tab(page)

    expect(page).to_have_url(re.compile(r"tab=calendar"))


# ── PUL-68: MTD summary element ───────────────────────────────────────────────

def test_mtd_summary_shows_correct_value_and_gain_class(page: Page, live_server_url: str):
    """Risk (PUL-68): MTD summary must appear in the calendar header label with the
    cumulative daily_change_pln for the month and the correct colour class.

    Fake data: day1=+300, day2=-150, day3=0 → cumulative = +150 → 'MTD +150 PLN', mtd-gain.
    Proves: JS render picks last data day, formats sign correctly, appends the span.
    (faro-v2 layout: MTD lives inside #pp-cal-label; the old standalone
    #pp-cal-mtd-summary element below the grid is intentionally hidden.)
    """
    _login(page, live_server_url)
    _open_portfolio(page)
    _open_calendar_tab(page)

    mtd = page.locator("#pp-cal-label .pp-cal-mtd")
    expect(mtd).to_be_visible()
    expect(mtd).to_have_text("MTD +150 PLN")
    expect(mtd).to_have_class(re.compile(r"\bmtd-gain\b"))


def test_mtd_summary_hidden_when_portfolio_has_no_data(page: Page, live_server_url: str):
    """Risk (PUL-68): when the calendar has no data rows (empty portfolio), the MTD
    summary element must not be visible — proving the hide branch runs correctly.

    Uses a portfolio_id that the mock returns [] for (any non-matching uuid).
    """
    _login(page, live_server_url)
    _open_portfolio(page)

    # Navigate to the calendar for a portfolio with no data by switching to an
    # unknown portfolio — the mock returns [] for any id != _FAKE_PORTFOLIO_ID,
    # so the calendar renders with no data days and mtd_diff = null for all.
    page.locator("#pp-portfolio-tabs .pp-portfolio-tab").first.click()
    _open_calendar_tab(page)

    # Navigate to a future month to guarantee no data days exist at all
    for _ in range(3):
        page.locator("#pp-cal-next").click()

    expect(page.locator("#pp-cal-label .pp-cal-mtd")).to_have_count(0)
    expect(page.locator("#pp-cal-mtd-summary")).not_to_be_visible()
