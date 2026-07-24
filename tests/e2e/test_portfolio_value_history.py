"""E2E tests — portfolio value-history line charts under the Kalendarz view.

Risk (PUL-89 → PUL-91): the value-over-time chart must render below the calendar
grid from real `GET /api/portfolio/history` data (auth → routing → API → inline-SVG
render). PUL-91 splits it into TWO charts — the active portfolio AND the "Wszystkie"
aggregate — with dynamic per-portfolio titles, a single shared range switcher driving
both, and a shared Wartość↔Zysk/strata toggle that redraws both from cache. When the
active tab is already "Wszystkie", only the single aggregate chart shows. These are
cross-boundary and rendered-UI risks a unit test can't prove.

Seed: tests/e2e/test_portfolio_calendar.py
Fixture data: conftest._FAKE_HISTORY_ROWS (active) / _FAKE_HISTORY_ROWS_ALL (aggregate)
  → active   value_pln [10000, 10150, 10120], pnl_pln [300, 450, 420]  → value 10120, P&L 420
  → aggregate value_pln [20000, 20300, 20240], pnl_pln [600, 900, 840] → value 20240, P&L 840
Locators are scoped to #pp-history-block-active / #pp-history-block-all because the
aggregate title text appears in both all-mode (single chart) and non-all-mode (chart #2).
"""
from playwright.sync_api import Page, expect

from tests.e2e.conftest import _FAKE_PORTFOLIO_ID, e2e_login_email

_ACTIVE = "#pp-history-block-active"
_ALL = "#pp-history-block-all"


def _login(page: Page, base_url: str) -> None:
    # PUL-74: per-user views are JWT-only — log in via the e-mail form.
    e2e_login_email(page, base_url)


def _open_portfolio_glowny(page: Page) -> None:
    page.get_by_role("button", name="Mój portfel").click()
    # PUL-90: default tab is read-only "Wszystkie" — select Główny for a specific
    # portfolio, so BOTH the active chart and the aggregate chart render (PUL-91).
    page.locator("#pp-portfolio-tabs .pp-portfolio-tab", has_text="Główny").click()


def _open_portfolio_wszystkie(page: Page) -> None:
    page.get_by_role("button", name="Mój portfel").click()
    page.locator("#pp-portfolio-tabs .pp-portfolio-tab", has_text="Wszystkie").click()


def _open_calendar_tab(page: Page) -> None:
    # The value charts live inside the Kalendarz view (design revised in PUL-89:
    # moved out of a standalone tab to below the calendar grid).
    page.locator("#pp-view-tabs").get_by_role("button", name="Kalendarz").click()
    expect(page.locator("#pp-calendar-wrap")).to_be_visible()


def test_both_charts_render_with_dynamic_titles(page: Page, live_server_url: str):
    """Risk: with a specific portfolio active, opening Kalendarz must render TWO
    value-history charts — the active portfolio (dynamic genitive title) and the
    "Wszystkie" aggregate — each with its own drawn SVG, proving both series reach
    the inline-SVG renderer across auth → routing → API → DOM."""
    _login(page, live_server_url)
    _open_portfolio_glowny(page)
    _open_calendar_tab(page)

    active = page.locator(_ACTIVE)
    aggregate = page.locator(_ALL)

    # Dynamic titles: active portfolio in genitive, aggregate constant.
    expect(active.get_by_role("heading", name="Wartość portfela głównego w czasie")).to_be_visible()
    expect(aggregate.get_by_role("heading", name="Wartość wszystkich portfeli w czasie")).to_be_visible()

    # Both charts draw a real line (not the empty state).
    expect(active.locator(".pp-hist-svg polyline")).to_be_visible()
    expect(aggregate.locator(".pp-hist-svg polyline")).to_be_visible()

    # Each chart shows its own value header (independent data).
    expect(active.locator(".pp-hist-val")).to_contain_text("PLN")
    expect(aggregate.locator(".pp-hist-val")).to_contain_text("PLN")


def test_wszystkie_tab_shows_single_aggregate_chart(page: Page, live_server_url: str):
    """Risk: when the active tab IS "Wszystkie", only ONE chart (the aggregate) must
    render — the active-portfolio block is redundant and must be hidden."""
    _login(page, live_server_url)
    _open_portfolio_wszystkie(page)
    _open_calendar_tab(page)

    aggregate = page.locator(_ALL)
    expect(aggregate).to_be_visible()
    expect(aggregate.get_by_role("heading", name="Wartość wszystkich portfeli w czasie")).to_be_visible()
    expect(aggregate.locator(".pp-hist-svg polyline")).to_be_visible()

    # The active-portfolio chart block must NOT be shown in all-mode.
    expect(page.locator(_ACTIVE)).to_be_hidden()


def test_range_switch_refetches_both_charts(page: Page, live_server_url: str):
    """Risk: the single range switcher must refetch BOTH series — one click issues a
    fresh GET /api/portfolio/history at the new range for the active portfolio AND for
    the aggregate (portfolio_id=all). A regression to single-chart behavior would fire
    only one."""
    _login(page, live_server_url)
    _open_portfolio_glowny(page)
    _open_calendar_tab(page)
    expect(page.locator(f"{_ACTIVE} .pp-hist-svg")).to_be_visible()
    expect(page.locator(f"{_ALL} .pp-hist-svg")).to_be_visible()

    seen: list[str] = []
    page.on(
        "request",
        lambda req: seen.append(req.url)
        if ("/api/portfolio/history" in req.url and "range=1m" in req.url)
        else None,
    )

    page.locator("#pp-history-ranges").get_by_role("button", name="1M", exact=True).click()

    # Both charts redraw only after their own fetch resolves — once both SVGs are
    # visible again, both 1M requests have fired.
    expect(page.locator(f"{_ACTIVE} .pp-hist-svg")).to_be_visible()
    expect(page.locator(f"{_ALL} .pp-hist-svg")).to_be_visible()
    assert any("portfolio_id=all" in u for u in seen), f"aggregate not refetched: {seen}"
    assert any(f"portfolio_id={_FAKE_PORTFOLIO_ID}" in u for u in seen), f"active not refetched: {seen}"


def test_metric_toggle_redraws_both_from_cache_without_refetch(page: Page, live_server_url: str):
    """Risk: the shared Wartość↔Zysk/strata toggle must redraw BOTH charts from their
    already-fetched payloads (both value_pln and pnl_pln travel in every point) WITHOUT
    a new network call — a refactor that wires the toggle to refetch would regress this."""
    _login(page, live_server_url)
    _open_portfolio_glowny(page)
    _open_calendar_tab(page)

    active_val = page.locator(f"{_ACTIVE} .pp-hist-val")
    aggregate_val = page.locator(f"{_ALL} .pp-hist-val")
    expect(active_val).to_be_visible()
    expect(aggregate_val).to_be_visible()
    # Starts on the value metric — active P&L 420 / aggregate P&L 840 absent.
    expect(active_val).not_to_contain_text("420")
    expect(aggregate_val).not_to_contain_text("840")

    # Count history requests fired AFTER the initial load; the toggle must add none.
    history_calls: list[str] = []
    page.on(
        "request",
        lambda req: history_calls.append(req.url) if "/api/portfolio/history" in req.url else None,
    )

    page.locator("#pp-history-metrics").get_by_role("button", name="Zysk/strata", exact=True).click()

    # Redraw is synchronous from cache: once both headers show their current P&L,
    # any refetch would already have fired — so the counter is a reliable check.
    expect(active_val).to_contain_text("420")
    expect(aggregate_val).to_contain_text("840")
    assert history_calls == [], f"metric toggle must not refetch, saw: {history_calls}"
