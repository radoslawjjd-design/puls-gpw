"""E2E tests — portfolio value-history line chart under the Kalendarz view (PUL-89).

Risk: the value-over-time chart must render below the calendar grid from real
`GET /api/portfolio/history` data (auth → routing → API → inline-SVG render), the
range switcher must actually refetch, and the Wartość↔Zysk/strata toggle must
redraw from the cached payload WITHOUT hitting the network. These are cross-boundary
and rendered-UI risks a unit test can't prove.

Seed: tests/e2e/test_portfolio_calendar.py
Fixture data: conftest._FAKE_HISTORY_ROWS via _fake_get_portfolio_history
  → value_pln [10000, 10150, 10120], pnl_pln [300, 450, 420]
  → current value = 10120 ("... PLN"); current P&L = 420 ("420 PLN")
"""
from playwright.sync_api import Page, expect

from tests.e2e.conftest import e2e_login_email


def _login(page: Page, base_url: str) -> None:
    # PUL-74: per-user views are JWT-only — log in via the e-mail form.
    e2e_login_email(page, base_url)


def _open_portfolio(page: Page) -> None:
    page.get_by_role("button", name="Mój portfel").click()
    # PUL-90: default tab is read-only "Wszystkie" — select Główny for the editable view.
    page.locator("#pp-portfolio-tabs .pp-portfolio-tab", has_text="Główny").click()


def _open_calendar_tab(page: Page) -> None:
    # The value chart lives inside the Kalendarz view (design revised in PUL-89:
    # moved out of a standalone tab to below the calendar grid).
    page.locator("#pp-view-tabs").get_by_role("button", name="Kalendarz").click()
    expect(page.locator("#pp-calendar-wrap")).to_be_visible()


def test_value_chart_renders_under_calendar(page: Page, live_server_url: str):
    """Risk: opening Kalendarz must render the value-history SVG chart and the value
    header below the calendar grid — proving /api/portfolio/history data reaches the
    inline-SVG renderer across auth → routing → API → DOM."""
    _login(page, live_server_url)
    _open_portfolio(page)
    _open_calendar_tab(page)

    section = page.locator("#pp-history-section")
    expect(section.get_by_role("heading", name="Wartość portfela w czasie")).to_be_visible()

    # The drawn line chart (not the empty state) must be present.
    expect(page.locator("#pp-history-chart .pp-hist-svg")).to_be_visible()
    expect(page.locator("#pp-history-chart .pp-hist-svg polyline")).to_be_visible()

    # The value header shows the current portfolio value (10120) in PLN.
    expect(page.locator("#pp-history-chart .pp-hist-val")).to_contain_text("PLN")


def test_range_switch_refetches_history(page: Page, live_server_url: str):
    """Risk: clicking a range button must issue a fresh GET /api/portfolio/history
    with the new range — proving the switcher is wired to the network, not a no-op."""
    _login(page, live_server_url)
    _open_portfolio(page)
    _open_calendar_tab(page)
    expect(page.locator("#pp-history-chart .pp-hist-svg")).to_be_visible()

    with page.expect_response(
        lambda r: "/api/portfolio/history" in r.url and "range=1m" in r.url
    ) as resp_info:
        page.locator("#pp-history-ranges").get_by_role("button", name="1M", exact=True).click()

    assert resp_info.value.status == 200
    # Chart is still rendered after the refetch+redraw.
    expect(page.locator("#pp-history-chart .pp-hist-svg")).to_be_visible()


def test_metric_toggle_redraws_from_cache_without_refetch(page: Page, live_server_url: str):
    """Risk: the Wartość↔Zysk/strata toggle must redraw from the already-fetched
    payload (both value_pln and pnl_pln travel in every point) WITHOUT a new network
    call — a refactor that wires the toggle to refetch would regress this."""
    _login(page, live_server_url)
    _open_portfolio(page)
    _open_calendar_tab(page)

    value_header = page.locator("#pp-history-chart .pp-hist-val")
    expect(value_header).to_be_visible()
    # Starts on the value metric — current value is 10120, so "420" is absent.
    expect(value_header).not_to_contain_text("420")

    # Count history requests fired AFTER the initial load; the toggle must add none.
    history_calls: list[str] = []
    page.on(
        "request",
        lambda req: history_calls.append(req.url) if "/api/portfolio/history" in req.url else None,
    )

    page.locator("#pp-history-metrics").get_by_role("button", name="Zysk/strata", exact=True).click()

    # Redraw is synchronous from cache: once the header shows the current P&L (420),
    # any refetch would already have fired — so the counter is a reliable check.
    expect(value_header).to_contain_text("420")
    assert history_calls == [], f"metric toggle must not refetch, saw: {history_calls}"
