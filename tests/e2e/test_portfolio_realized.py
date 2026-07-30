"""E2E tests — the Zrealizowane tab and the free-cash position (PUL-95 Phase 8).

Both features answer the same question the dividends tab left open: what did the
account actually make, and what is sitting on it uninvested. The risks worth a
browser test are the ones a unit test cannot reach — the tab is a fourth entry in
a mode handler that has to be wired in two separate places, and the cash row is a
reserved ticker that must not leak into the UI as `_CASH`.
"""
import re

from playwright.sync_api import Page, expect

from tests.e2e.conftest import e2e_login_email


def _login_and_open(page: Page, base_url: str) -> None:
    e2e_login_email(page, base_url)
    page.get_by_role("button", name="Mój portfel").click()
    page.locator("#pp-portfolio-tabs .pp-portfolio-tab", has_text="Główny").click()


def _open_realized(page: Page) -> None:
    with page.expect_response(re.compile(r"/api/portfolio/realized")):
        page.locator('#pp-view-tabs .pp-view-tab[data-mode="realized"]').click()


def test_realized_tab_fetches_and_renders(page: Page, live_server_url: str):
    """Risk: the mode handler is two adjacent blocks and a new mode has to reach
    both. Wiring only the display block opens the tab onto an empty panel — the
    exact failure the dividends tab was reviewed for."""
    _login_and_open(page, live_server_url)

    _open_realized(page)

    expect(page.locator("#pp-realized-wrap")).to_be_visible()
    expect(page.locator("#pp-real-by-ticker")).to_contain_text("PKO")


def test_realized_uses_the_fifo_cost_basis_not_the_average(page: Page, live_server_url: str):
    """The fixture buys 100 @ 40 then 100 @ 60 and sells 100 @ 50.

    FIFO consumes the 40 lot, so the result is +1 000. An average-cost basis
    would show 0 and a LIFO basis −1 000 — three different numbers from the same
    trades, which is why this is asserted end to end and not only in the unit test.
    """
    _login_and_open(page, live_server_url)

    _open_realized(page)

    row = page.locator("#pp-real-by-ticker tr", has_text="PKO")
    # \s, not a literal space: pl-PL groups thousands with a non-breaking space.
    expect(row).to_contain_text(re.compile(r"4\s?000,00 zł"))   # cost = 100 x 40
    expect(row).to_contain_text(re.compile(r"5\s?000,00 zł"))   # proceeds = 100 x 50
    expect(row).to_contain_text(re.compile(r"\+1\s?000,00 zł"))


def test_a_sale_without_a_recorded_purchase_is_disclosed(page: Page, live_server_url: str):
    """CDR is sold in the fixture with no buy behind it, so its whole proceeds
    land in the result. Showing that as profit without saying so would be a lie
    with a number attached."""
    _login_and_open(page, live_server_url)

    _open_realized(page)

    note = page.locator("#pp-real-note")
    expect(note).to_be_visible()
    expect(note).to_contain_text("CDR")


def test_year_switch_refetches_without_leaving_the_tab(page: Page, live_server_url: str):
    """The year pills reuse .pp-view-tab, which the mode handler also binds — the
    trap the dividends and range pills both had to dodge."""
    _login_and_open(page, live_server_url)
    _open_realized(page)

    with page.expect_response(re.compile(r"/api/portfolio/realized.*year=2026")):
        page.locator('#pp-real-years .pp-view-tab[data-year="2026"]').click()

    expect(page.locator("#pp-realized-wrap")).to_be_visible()
    expect(page.locator('#pp-real-years .pp-view-tab[data-year="2026"]')).to_have_class(
        re.compile(r"active")
    )
    # 2026 holds only the PKO sale; CDR's unmatched 2025 sale must drop out.
    expect(page.locator("#pp-real-note")).to_be_hidden()


def test_switching_away_hides_the_realized_panel(page: Page, live_server_url: str):
    """A panel missing from the display block stays visible over the next view."""
    _login_and_open(page, live_server_url)
    _open_realized(page)
    expect(page.locator("#pp-realized-wrap")).to_be_visible()

    page.locator('#pp-view-tabs .pp-view-tab[data-mode="table"]').click()

    expect(page.locator("#pp-realized-wrap")).to_be_hidden()
    expect(page.locator("#pp-table-wrap")).to_be_visible()


def test_the_cash_position_is_not_labelled_with_its_storage_ticker(
    page: Page, live_server_url: str
):
    """Risk: `_CASH` is a storage sentinel. Rendered raw in the ticker column it
    reads as a broken import, and its cell must not offer a company-announcements
    link that would search for a ticker no exchange lists."""
    _login_and_open(page, live_server_url)
    # The wallet click fires a positions fetch. Without waiting for it to render,
    # it resolves after the fixture below and silently replaces it — which is how
    # this test first "failed" against working code.
    expect(page.locator("#pp-tbody")).to_contain_text("PKO")
    page.evaluate(
        """() => {
            _ppPositions = [{
              ticker: '_CASH', company_name: 'Wolne środki', shares: 2160.11,
              avg_buy_price: 1.0, current_price: 1.0, daily_change_pct: 0.0,
              daily_change_per_share: 0.0, pnl_pln: 0.0, pnl_pct: 0.0,
              price_as_of: '2026-07-30', price_history: null,
            }];
            _renderPortfolioTable(_ppPositions);
        }"""
    )

    row = page.locator("#pp-tbody tr").first
    expect(row).to_contain_text("Wolne środki")
    expect(row).to_contain_text("PLN")
    expect(page.locator("#pp-tbody")).not_to_contain_text("_CASH")
    expect(row.locator(".pp-ticker-link")).to_have_count(0)


def test_cash_counts_towards_the_portfolio_value(page: Page, live_server_url: str):
    """Cash that does not reach the value tile is a number the user cannot use.

    Priced at 1.00 PLN, so the tile has to read the balance back exactly — and
    the daily-change tile must stay at zero, because cash does not move.
    """
    _login_and_open(page, live_server_url)
    # The wallet click fires a positions fetch. Without waiting for it to render,
    # it resolves after the fixture below and silently replaces it — which is how
    # this test first "failed" against working code.
    expect(page.locator("#pp-tbody")).to_contain_text("PKO")
    page.evaluate(
        """() => {
            _ppPositions = [{
              ticker: '_CASH', company_name: 'Wolne środki', shares: 2160.11,
              avg_buy_price: 1.0, current_price: 1.0, daily_change_pct: 0.0,
              daily_change_per_share: 0.0, pnl_pln: 0.0, pnl_pct: 0.0,
              price_as_of: '2026-07-30', price_history: null,
            }];
            _renderPortfolioTable(_ppPositions);
        }"""
    )

    expect(page.locator("#pp-sum-value")).to_contain_text(re.compile(r"2\s?160,11"))
    expect(page.locator("#pp-sum-daily")).to_contain_text("0,00")


def test_the_browser_tab_signals_a_request_in_flight(page: Page, live_server_url: str):
    """Risk: the whole point is a signal on the tab, and the native spinner cannot
    be driven from JS, so the favicon and title are what carry it.

    The icon must also actually MOVE. The first attempt used an animated SVG,
    which browsers render as a still image — SMIL in a favicon is ignored — so it
    shipped as a frozen arc that read as a broken icon. Asserting only that the
    href changed once would still have passed. Comparing two frames over time is
    what pins the animation.

    Held mid-flight on purpose — the indicator only exists between the click and
    the response, so asserting after the request settles would pass regardless.
    The 300ms debounce has to be outwaited too, which is why the hold is long.
    """
    _login_and_open(page, live_server_url)
    held = {"done": False}

    def hold(route):
        # First matching request only: a handler that sleeps on every match is
        # still armed at teardown, and sleeping on a closing page poisons the
        # next test's setup.
        if not held["done"]:
            held["done"] = True
            page.wait_for_timeout(2500)
        route.continue_()

    try:
        page.route("**/api/portfolio/realized**", hold)
        page.locator('#pp-view-tabs .pp-view-tab[data-mode="realized"]').click()

        expect(page).to_have_title(re.compile(r"^⟳"))
        first = page.get_attribute("#app-favicon", "href")
        assert first.startswith("data:image/png"), "frames are drawn on a canvas"
        # ~70ms per frame, so this straddles several.
        page.wait_for_timeout(300)
        second = page.get_attribute("#app-favicon", "href")
        assert second != first, "the icon has to advance, not sit on one frame"
    finally:
        page.unroute_all(behavior="ignoreErrors")

    # And it must go back — an indicator that never clears is worse than none.
    expect(page).not_to_have_title(re.compile(r"^⟳"))
    assert page.get_attribute("#app-favicon", "href").endswith(".png?v=2")
