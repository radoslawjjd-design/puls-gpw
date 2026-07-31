"""E2E tests — "Mój portfel" at a phone viewport (PUL-108).

The reported symptoms were three: the dividends table slightly cut off, the
realized table losing three whole columns, and the logo topbar sliding away when
the page was dragged right. They are one defect. `position: sticky` pins along
the axis its scroll container actually scrolls, so the moment <body> overflows
horizontally the topbar travels with everything else. Fix the overflow and the
topbar stops moving on its own.

These are geometry assertions on purpose. "Ucina tabelę" is a measurable claim —
a cell whose right edge lies past the viewport — and a screenshot comparison
would answer a different, more brittle question.

Seed: tests/e2e/test_portfolio_realized.py
"""
import re

from playwright.sync_api import Page, expect

from tests.e2e.conftest import e2e_login_email

_PHONE = {"width": 360, "height": 740}

_MODES = ["table", "treemap", "calendar", "dividends", "realized"]


def _open_on_a_phone(page: Page, base_url: str) -> None:
    page.set_viewport_size(_PHONE)
    e2e_login_email(page, base_url)
    page.get_by_role("button", name="Mój portfel").click()
    page.locator("#pp-portfolio-tabs .pp-portfolio-tab", has_text="Główny").click()


def _page_overflow(page: Page) -> int:
    return page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )


def test_no_tab_makes_the_page_scroll_sideways(page: Page, live_server_url: str):
    """Risk: a single wide element hands its overflow to the page, and everything
    anchored to the viewport — the topbar above all — goes along for the ride."""
    _open_on_a_phone(page, live_server_url)

    for mode in _MODES:
        tab = page.locator(f'#pp-view-tabs .pp-view-tab[data-mode="{mode}"]')
        tab.click()
        # Wait on state, never on a timeout: the tab carries .active only once the
        # mode handler has run, which is also when its panel has been rendered.
        expect(tab).to_have_class(re.compile(r"\bactive\b"))
        expect(page.locator("#portfolio-positions-view")).to_be_visible()
        overflow = _page_overflow(page)
        assert overflow <= 1, f"tab '{mode}' widens the page by {overflow}px"


def test_the_realized_table_needs_no_swipe_to_reach_its_last_column(
    page: Page, live_server_url: str
):
    """Risk: seven columns on a 360 px screen put three of them past the right
    edge. The card layout turns them into seven labelled rows — the check is that
    no cell ends beyond the viewport, whichever way that was achieved."""
    _open_on_a_phone(page, live_server_url)
    with page.expect_response(re.compile(r"/api/portfolio/realized")):
        page.locator('#pp-view-tabs .pp-view-tab[data-mode="realized"]').click()
    expect(page.locator("#pp-real-by-ticker")).to_contain_text("PKO")

    right_edges = page.evaluate(
        """() => Array.from(document.querySelectorAll('#pp-real-by-ticker td'))
                     .map(td => td.getBoundingClientRect().right)"""
    )
    assert right_edges, "no cells rendered"
    assert max(right_edges) <= _PHONE["width"] + 1, (
        f"a cell reaches {max(right_edges)}px on a {_PHONE['width']}px screen"
    )


def test_the_realized_card_names_the_numbers_it_stacks(page: Page, live_server_url: str):
    """A column that becomes a row loses its header, so the label has to travel
    with the cell. Without it the card is a column of unexplained amounts."""
    _open_on_a_phone(page, live_server_url)
    with page.expect_response(re.compile(r"/api/portfolio/realized")):
        page.locator('#pp-view-tabs .pp-view-tab[data-mode="realized"]').click()

    row = page.locator("#pp-real-by-ticker tr", has_text="PKO")
    for label in ("Sprzedano", "Przychód", "Koszt", "Wynik", "Zwrot"):
        expect(row.locator(f'td[data-label="{label}"]')).to_have_count(1)


def test_the_dividends_table_needs_no_swipe_either(page: Page, live_server_url: str):
    """The dividends table was only "slightly" cut off, which is the same defect
    with fewer columns — and the same fix."""
    _open_on_a_phone(page, live_server_url)
    with page.expect_response(re.compile(r"/api/portfolio/dividends")):
        page.locator('#pp-view-tabs .pp-view-tab[data-mode="dividends"]').click()
    expect(page.locator("#pp-div-by-ticker")).to_be_visible()

    right_edges = page.evaluate(
        """() => Array.from(document.querySelectorAll('#pp-div-by-ticker td'))
                     .map(td => td.getBoundingClientRect().right)"""
    )
    if right_edges:
        assert max(right_edges) <= _PHONE["width"] + 1


def test_every_view_tab_is_reachable_without_a_sideways_drag(
    page: Page, live_server_url: str
):
    """Five tabs do not fit 360 px in one line. Wrapping keeps them all on screen;
    a scroll strip would have handed the overflow straight back to the page."""
    _open_on_a_phone(page, live_server_url)

    boxes = page.evaluate(
        """() => Array.from(document.querySelectorAll('#pp-view-tabs .pp-view-tab'))
                     .map(b => b.getBoundingClientRect().right)"""
    )
    assert len(boxes) == len(_MODES)
    assert max(boxes) <= _PHONE["width"] + 1, f"a view tab reaches {max(boxes)}px"


def test_the_topbar_stays_pinned_after_a_sideways_drag(page: Page, live_server_url: str):
    """The original complaint, asserted directly: scroll right as far as the page
    allows and the topbar must not have moved. With the overflow gone there is
    nowhere to scroll to, which is exactly why it holds."""
    _open_on_a_phone(page, live_server_url)

    before = page.locator(".topbar").bounding_box()
    page.evaluate("() => window.scrollTo(500, 0)")
    after = page.locator(".topbar").bounding_box()
    assert before is not None and after is not None
    assert abs(after["x"] - before["x"]) <= 1, f"topbar moved from {before['x']} to {after['x']}"


# ── Card colours and the tap that must not repaint them ──────────────────────

# Relative luminance of a computed `rgb()/rgba()` string, so a contrast claim can
# be made without hardcoding either theme's palette — the point is that text and
# the surface behind it are not the same shade, whichever shade that is.
_LUMINANCE_JS = """
  const lum = s => {
    const [r, g, b] = s.match(/[\d.]+/g).slice(0, 3).map(Number).map(v => {
      const c = v / 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
"""


def _card_contrast(page: Page, table_id: str) -> dict:
    return page.evaluate(
        _LUMINANCE_JS
        + """
        (id) => {
          const row = document.querySelector('#' + id + ' tbody tr');
          const value = row.querySelectorAll('td')[2];
          const bg = getComputedStyle(row).backgroundColor;
          const fg = getComputedStyle(value).color;
          const label = getComputedStyle(value, '::before').color;
          return { bg, fg, ratio: Math.abs(lum(bg) - lum(fg)),
                   labelRatio: Math.abs(lum(bg) - lum(label)) };
        }""",
        table_id,
    )


def _switch_theme(page: Page, theme: str) -> None:
    page.evaluate("t => { localStorage.setItem('faro_theme', t); _applyTheme(t); }", theme)


def test_the_stacked_cards_stay_legible_in_both_themes(page: Page, live_server_url: str):
    """The card background was a hardcoded #fff with no dark counterpart, so dark
    mode put near-white values (--text) on white and they simply disappeared —
    only the muted grey labels showed, which is what "szary jest nieczytelny"
    was describing. Luminance, not a palette, because the claim is about contrast."""
    _open_on_a_phone(page, live_server_url)

    for theme in ("light", "dark"):
        _switch_theme(page, theme)
        for tab, table_id in (("Dywidendy", "pp-div-by-ticker"), ("Zrealizowane", "pp-real-by-ticker")):
            page.locator("#pp-view-tabs").get_by_role("button", name=tab).click()
            expect(page.locator(f"#{table_id} table").first).to_be_visible()
            seen = _card_contrast(page, table_id)
            assert seen["ratio"] > 0.3, f"{theme}/{tab}: value on card is {seen}"
            assert seen["labelRatio"] > 0.15, f"{theme}/{tab}: label on card is {seen}"


def test_touching_a_card_does_not_repaint_it(page: Page, live_server_url: str):
    """A card is a layout, not a control. Mobile browsers apply :hover on tap, so
    the desktop table's row tint reads on a phone as "touching it changed
    something"."""
    _open_on_a_phone(page, live_server_url)
    page.locator("#pp-view-tabs").get_by_role("button", name="Zrealizowane").click()
    expect(page.locator("#pp-real-by-ticker table").first).to_be_visible()

    row = page.locator("#pp-real-by-ticker tbody tr").first
    cell = row.locator("td").nth(2)
    before = cell.evaluate("e => getComputedStyle(e).backgroundColor")
    row.hover()
    after = cell.evaluate("e => getComputedStyle(e).backgroundColor")
    assert before == after, f"tap repainted the card: {before} → {after}"
