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
    # PUL-90: default tab is read-only "Wszystkie" — select Główny for the editable view.
    page.locator("#pp-portfolio-tabs .pp-portfolio-tab", has_text="Główny").click()


def _open_calendar_tab(page: Page) -> None:
    page.locator("#pp-view-tabs").get_by_role("button", name="Kalendarz").click()
    expect(page.locator("#pp-calendar-wrap")).to_be_visible()


def test_calendar_tab_exists_and_shows_grid_on_click(page: Page, live_server_url: str):
    """Risk: Kalendarz tab must exist alongside Portfel and Treemapa, and clicking it
    must reveal the calendar container and a non-empty grid."""
    _login(page, live_server_url)
    _open_portfolio(page)

    view_tabs = page.locator("#pp-view-tabs")
    expect(view_tabs.get_by_role("button", name="Portfel")).to_be_visible()
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


def test_the_day_number_does_not_sit_on_top_of_the_amount(page: Page, live_server_url: str):
    """Risk (PUL-110): the day number used to be absolutely positioned over a centred
    amount. Geometry, not appearance, is the thing to assert — two boxes fighting for
    the same space is exactly what "nachodzą na siebie" means."""
    _login(page, live_server_url)
    _open_portfolio(page)
    _open_calendar_tab(page)

    cell = page.locator("#pp-cal-grid .pp-cal-gain").first
    expect(cell).to_be_visible()
    day_box = cell.locator(".pp-cal-day").bounding_box()
    pnl_box = cell.locator(".pp-cal-pnl").bounding_box()
    assert day_box is not None and pnl_box is not None
    # The day's bottom edge must not reach past the amount's top edge.
    assert day_box["y"] + day_box["height"] <= pnl_box["y"] + 1, (
        f"day {day_box} overlaps amount {pnl_box}"
    )


def test_the_picker_reaches_last_year_without_twelve_clicks(page: Page, live_server_url: str):
    """Risk (PUL-110): stepping to a month a year back cost twelve clicks and twelve
    fetches to change one number. The jump must land on the chosen month and leave the
    URL saying the same thing the arrows would have."""
    _login(page, live_server_url)
    _open_portfolio(page)
    _open_calendar_tab(page)

    target_year = date.today().year - 1
    page.get_by_role("button", name="Wybierz miesiąc i rok").click()
    page.locator("#pp-cal-picker-year").select_option(str(target_year))
    page.locator("#pp-cal-picker-month").select_option("1")
    page.get_by_role("button", name="Pokaż").click()

    expect(page.locator("#pp-cal-label")).to_contain_text(f"Styczeń {target_year}")
    expect(page.locator("#pp-cal-picker")).to_be_hidden()
    page.wait_for_url(re.compile(rf"year={target_year}"))
    page.wait_for_url(re.compile(r"month=1(&|$)"))


def test_the_picker_offers_no_month_that_has_not_happened_yet(page: Page, live_server_url: str):
    """Risk (PUL-110): a future month has no P&L to show, so offering it spends a fetch
    to render an empty grid. The arrows keep their old freedom — this is about the picker."""
    _login(page, live_server_url)
    _open_portfolio(page)
    _open_calendar_tab(page)

    page.get_by_role("button", name="Wybierz miesiąc i rok").click()
    months = page.locator("#pp-cal-picker-month option")
    expect(months).to_have_count(date.today().month)

    # A past year within the wallet's life is unrestricted — the cut is about "not
    # yet", not about the picker.
    page.locator("#pp-cal-picker-year").select_option(str(date.today().year - 1))
    expect(months).to_have_count(12)


# ── PUL-115: the picker is bounded by the wallet, not by a round number ───────

def test_the_picker_offers_no_year_before_the_wallet_existed(
    page: Page, live_server_url: str
):
    """Risk (PUL-115): the floor was a flat ten years, chosen because nothing on the
    client knew when the wallet began. Five of those years the API rejected outright
    (422), so the picker could produce "Błąd ładowania kalendarza" on its own.

    Fixture inception: conftest._FAKE_INCEPTION — 1 March, two years back.
    """
    _login(page, live_server_url)
    _open_portfolio(page)
    _open_calendar_tab(page)

    page.get_by_role("button", name="Wybierz miesiąc i rok").click()
    years = page.locator("#pp-cal-picker-year option")
    values = years.evaluate_all("opts => opts.map(o => Number(o.value))")

    assert min(values) == date.today().year - 2, values
    assert max(values) == date.today().year, values


def test_the_picker_offers_no_month_before_the_wallet_held_anything(
    page: Page, live_server_url: str
):
    """Risk (PUL-115): a bound that only rounds to the year still offers January and
    February of an inception year that starts in March — months with nothing in them."""
    _login(page, live_server_url)
    _open_portfolio(page)
    _open_calendar_tab(page)

    page.get_by_role("button", name="Wybierz miesiąc i rok").click()
    page.locator("#pp-cal-picker-year").select_option(str(date.today().year - 2))

    months = page.locator("#pp-cal-picker-month option")
    values = months.evaluate_all("opts => opts.map(o => Number(o.value))")
    assert values == list(range(3, 13)), values


# ── PUL-111: what happened on this day ───────────────────────────────────────

def test_clicking_a_closed_day_says_why_the_exchange_was_shut(
    page: Page, live_server_url: str
):
    """Risk (PUL-111): a blank tile looked the same whether the exchange was closed
    or we simply had no price for it. The reason is derived on the backend, so this
    proves the whole path: computed name → response → cell → dialog."""
    _login(page, live_server_url)
    _open_portfolio(page)
    _open_calendar_tab(page)

    # A weekend is the one closed day every month is guaranteed to have.
    page.locator("#pp-cal-grid .pp-cal-neutral").first.click()

    popup = page.locator("#pp-cal-popup-backdrop")
    expect(popup).to_be_visible()
    expect(page.locator("#pp-cal-popup-state")).to_contain_text("giełda nie prowadzi sesji")
    # A closed day has no P&L to report, and a "+0 PLN" there would read as a real
    # flat session — the same fabrication PUL-103 removed from the grid.
    expect(page.locator("#pp-cal-popup-pnl")).to_be_hidden()


def test_clicking_a_session_day_reports_its_numbers_and_no_closure_reason(
    page: Page, live_server_url: str
):
    """Risk (PUL-111): the popup must not answer "why was it closed" for a day that
    traded. Fixture: the month's first weekday is +300 on 2 of 2 priced positions."""
    _login(page, live_server_url)
    _open_portfolio(page)
    _open_calendar_tab(page)

    page.locator("#pp-cal-grid .pp-cal-gain").first.click()

    expect(page.locator("#pp-cal-popup-backdrop")).to_be_visible()
    expect(page.locator("#pp-cal-popup-state")).to_have_text("Sesja giełdowa")
    expect(page.locator("#pp-cal-popup-pnl")).to_contain_text("300,00 PLN")
    expect(page.locator("#pp-cal-popup-coverage")).to_contain_text("2 z 2")


def test_the_day_popup_closes_and_is_reachable_without_a_mouse(
    page: Page, live_server_url: str
):
    """Risk (PUL-111): the tiles are divs, so click alone would make the day context
    mouse-only, and a dialog with no Escape is a keyboard trap."""
    _login(page, live_server_url)
    _open_portfolio(page)
    _open_calendar_tab(page)

    cell = page.locator("#pp-cal-grid .pp-cal-gain").first
    cell.focus()
    cell.press("Enter")

    popup = page.locator("#pp-cal-popup-backdrop")
    expect(popup).to_be_visible()
    popup.press("Escape")
    expect(popup).to_be_hidden()
