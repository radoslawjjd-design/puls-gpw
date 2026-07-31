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
import re

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

    # X-axis endpoints carry the year — DD.MM alone is ambiguous once a range
    # crosses a year boundary (1r always does).
    # SVG <text> has no innerText, so read textContent.
    for chart in (active, aggregate):
        texts = [t.strip() for t in chart.locator(".pp-hist-axis").all_text_contents()]
        assert any(re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", t) for t in texts), (
            f"no DD.MM.YYYY axis label found, got {texts}"
        )


def test_backfill_note_is_reachable_without_a_mouse(page: Page, live_server_url: str):
    """Risk (PUL-100): the chart values a holding at its first known close on days
    before that close existed. In a finance app a silent assumption is the bug, so the
    disclosure must be genuinely reachable — a hover-only tooltip is unreachable by
    keyboard and on touch. Driving it with Enter proves the affordance is a real button
    and not a hover surface, and the aggregate chart (no backfilled holding in the fake)
    must carry no affordance at all."""
    _login(page, live_server_url)
    _open_portfolio_glowny(page)
    _open_calendar_tab(page)

    active = page.locator(_ACTIVE)
    info = active.get_by_role("button", name=re.compile("Szczegóły wyceny"))
    expect(info).to_be_visible()
    expect(info).to_have_attribute("aria-expanded", "false")

    # Keyboard alone: focus the affordance and activate it with Enter.
    info.focus()
    page.keyboard.press("Enter")

    expect(info).to_have_attribute("aria-expanded", "true")
    expect(active.get_by_text(re.compile(r"S2B.*brak notowań przed"))).to_be_visible()
    # The excluded branch is the worst case — a holding we couldn't price at all — so
    # it is exactly the message that must not rot untested.
    expect(active.get_by_text(re.compile(r"AAS.*pominięty w wycenie"))).to_be_visible()

    # A chart with nothing to disclose must not grow a dangling affordance.
    expect(
        page.locator(_ALL).get_by_role("button", name=re.compile("Szczegóły wyceny"))
    ).to_have_count(0)


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


_YEAR_SERIES_JS = """() => {
    // A year of trading days, weekends skipped — the same shape the endpoint
    // returns, which is what makes the index-vs-time distinction bite.
    const series = [];
    const d = new Date(2025, 0, 2);
    while (d < new Date(2025, 11, 20)) {
      const wd = d.getDay();
      if (wd !== 0 && wd !== 6) {
        const iso = d.getFullYear() + '-' +
          String(d.getMonth() + 1).padStart(2, '0') + '-' +
          String(d.getDate()).padStart(2, '0');
        series.push({ date: iso, value_pln: 10000 + series.length * 3, pnl_pln: series.length });
      }
      d.setDate(d.getDate() + 1);
    }
    _renderPortfolioHistory(
      { series, notes: [], excluded: [] },
      document.getElementById('pp-history-chart-active')
    );
    return series.length;
}"""


def test_the_chart_labels_more_than_its_two_endpoints(page: Page, live_server_url: str):
    """Risk (PUL-109): the axis carried exactly two dates — the first point and the
    last — so a reader could not tell where in the window a move happened."""
    _login(page, live_server_url)
    _open_portfolio_glowny(page)
    _open_calendar_tab(page)

    count = page.evaluate(_YEAR_SERIES_JS)
    assert count > 200, "fixture must span a real year of trading days"

    # SVG <text> is not an HTMLElement, so inner_text() refuses it — read the DOM text.
    texts = page.evaluate(
        """() => Array.from(
             document.querySelectorAll('#pp-history-chart-active .pp-hist-axis')
           ).map(t => t.textContent)"""
    )
    # Two endpoint dates + intermediate month ticks + the Y labels.
    month_ticks = [t for t in texts if re.fullmatch(r"[a-ząćęłńóśźż]{3} \d{2}", t)]
    assert 3 <= len(month_ticks) <= 6, f"expected 4-6 x ticks incl. endpoints, got {texts}"


def test_the_chart_ticks_land_on_month_boundaries_not_on_equal_index_steps(
    page: Page, live_server_url: str
):
    """Risk (PUL-109): the X axis is index-based and the series holds trading days
    only, so a month with a long holiday occupies fewer slots than its neighbour.
    Ticks spaced every n/5 points would drift off the month they claim to mark —
    the proof is that consecutive ticks are NOT evenly spaced in x."""
    _login(page, live_server_url)
    _open_portfolio_glowny(page)
    _open_calendar_tab(page)
    page.evaluate(_YEAR_SERIES_JS)

    xs = page.evaluate(
        """() => Array.from(
             document.querySelectorAll('#pp-history-chart-active .pp-hist-axis')
           ).filter(t => /^[a-ząćęłńóśźż]{3} \d{2}$/.test(t.textContent))
            .map(t => parseFloat(t.getAttribute('x')))"""
    )
    assert len(xs) >= 3
    gaps = [round(xs[i + 1] - xs[i], 1) for i in range(len(xs) - 1)]
    assert len(set(gaps)) > 1, f"ticks are evenly spaced ({gaps}) — that is index math, not dates"


def test_a_flat_series_does_not_stack_three_labels_on_one_baseline(
    page: Page, live_server_url: str
):
    """Risk (PUL-109): the Y axis gained a midpoint label. When every value is equal,
    max, midpoint and min share a baseline — printing all three overlays them.

    Selected by x, not by text-anchor: the right-hand endpoint date is anchored
    "end" too, and catching it made this test read three labels as two."""
    _login(page, live_server_url)
    _open_portfolio_glowny(page)
    _open_calendar_tab(page)

    page.evaluate(
        """() => _renderPortfolioHistory(
             { series: [
                 { date: '2026-07-01', value_pln: 5000, pnl_pln: 0 },
                 { date: '2026-07-02', value_pln: 5000, pnl_pln: 0 },
                 { date: '2026-07-03', value_pln: 5000, pnl_pln: 0 },
               ], notes: [], excluded: [] },
             document.getElementById('pp-history-chart-active'))"""
    )

    ys = page.evaluate(
        """() => Array.from(
             document.querySelectorAll('#pp-history-chart-active .pp-hist-axis')
           ).filter(t => t.getAttribute('x') === '44')
            .map(t => t.getAttribute('y'))"""
    )
    assert len(ys) == 1, f"expected a single Y label for a flat series, got {ys}"
