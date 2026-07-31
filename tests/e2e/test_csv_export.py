"""E2E tests — CSV exports: the calendar's own export and the filename convention.

Risk (PUL-104): two gaps in one surface. The calendar — the view that actually
carries the daily P&L — had no export at all. And every exported file was a
hardcoded constant (`portfel.csv`, `ogloszenia.csv`, `obserwowane.csv`), so a
second export landed as `portfel (1).csv` and a download folder held several
files that could no longer be told apart.

Both risks are only observable at the browser boundary: what the file is called
is decided by the anchor's `download` attribute, and what is inside it is built
from data that never leaves the page. A download is the only way to see either.

Seed: tests/e2e/test_portfolio_calendar.py
Fixture data: conftest._FAKE_CALENDAR_ROWS — the first three weekdays of the
current month at +300 / −150 / 0, one wallet ("Główny", type `glowny`).
"""
import re
from datetime import date

from playwright.sync_api import Page, expect

from tests.e2e.conftest import e2e_login_email


def _login(page: Page, base_url: str) -> None:
    # PUL-74: per-user views are JWT-only — log in via the e-mail form.
    e2e_login_email(page, base_url)


def _open_portfolio_glowny(page: Page) -> None:
    page.get_by_role("button", name="Mój portfel").click()
    # PUL-90: the default tab is the read-only "Wszystkie" aggregate.
    page.locator("#pp-portfolio-tabs .pp-portfolio-tab", has_text="Główny").click()


def _open_calendar_tab(page: Page) -> None:
    page.locator("#pp-view-tabs").get_by_role("button", name="Kalendarz").click()
    expect(page.locator("#pp-calendar-wrap")).to_be_visible()


def _rows_of(download) -> list[list[str]]:
    """Read a downloaded CSV the way Polish Excel does: BOM, semicolons, CRLF."""
    with open(download.path(), encoding="utf-8-sig") as fh:
        text = fh.read()
    return [line.split(";") for line in text.splitlines() if line]


def test_the_displayed_calendar_month_can_be_exported(page: Page, live_server_url: str):
    """Risk: the calendar carried the daily P&L and was the only view with no way to
    get it out of the browser. The file must hold the month on screen, for the wallet
    on screen, and name itself after both."""
    _login(page, live_server_url)
    _open_portfolio_glowny(page)
    _open_calendar_tab(page)

    with page.expect_download() as dl:
        page.locator("#pp-cal-actions").get_by_role("button", name="Eksport CSV").click()
    download = dl.value

    today = date.today()
    assert download.suggested_filename == f"Glowny_kalendarz_{today:%Y-%m}.csv"

    rows = _rows_of(download)
    assert rows[0][0] == "Data"
    # _FAKE_CALENDAR_ROWS: first weekday of the month is +300, second −150.
    amounts = [r[2] for r in rows[1:]]
    assert "300,00" in amounts
    assert "-150,00" in amounts


def test_the_calendar_export_sits_between_the_tabs_and_the_grid(
    page: Page, live_server_url: str
):
    """It first shipped under the legend, at the bottom of a long view — past the
    grid, the legend and two charts. Geometry, because "between the tabs and the
    calendar" is a claim about where it lands on screen, not about DOM order."""
    _login(page, live_server_url)
    _open_portfolio_glowny(page)
    _open_calendar_tab(page)

    tabs = page.locator("#pp-view-tabs").bounding_box()
    button = page.locator("#pp-cal-actions").bounding_box()
    grid = page.locator("#pp-cal-grid").bounding_box()
    assert tabs is not None and button is not None and grid is not None

    assert tabs["y"] + tabs["height"] <= button["y"] + 1, "the button is above the view tabs"
    assert button["y"] + button["height"] <= grid["y"] + 1, "the button is below the grid"


def test_a_day_without_a_session_exports_no_number_at_all(page: Page, live_server_url: str):
    """Risk: a weekend written as 0 reads in a spreadsheet as a real flat session —
    the same fabrication PUL-103 removed from the rendered calendar. A saved file
    outlives a grid, so the empty cell matters more here, not less."""
    _login(page, live_server_url)
    _open_portfolio_glowny(page)
    _open_calendar_tab(page)

    with page.expect_download() as dl:
        page.locator("#pp-cal-actions").get_by_role("button", name="Eksport CSV").click()

    rows = _rows_of(dl.value)
    idle = [r for r in rows[1:] if r[1] in ("weekend", "święto GPW", "brak danych")]
    assert idle, "the fixture month must contain at least one non-session day"
    for row in idle:
        # P&L, portfolio value, month-to-date — every one of them empty, not "0".
        assert row[2:5] == ["", "", ""], f"non-session day carries numbers: {row}"


def test_two_exports_of_different_things_get_different_names(page: Page, live_server_url: str):
    """Risk: three hardcoded constants meant positions and calendar collided with
    themselves on every repeat export. The wallet and the period have to be in the
    name — that is the whole point of the convention."""
    _login(page, live_server_url)
    _open_portfolio_glowny(page)

    with page.expect_download() as positions_dl:
        page.locator("#pp-table-actions").get_by_role("button", name="Eksport CSV").click()

    today = date.today()
    assert positions_dl.value.suggested_filename == f"Glowny_{today:%Y-%m-%d}.csv"

    _open_calendar_tab(page)
    with page.expect_download() as calendar_dl:
        page.locator("#pp-cal-actions").get_by_role("button", name="Eksport CSV").click()

    assert calendar_dl.value.suggested_filename != positions_dl.value.suggested_filename


def test_a_wallet_name_illegal_in_a_filename_still_produces_a_saveable_one(
    page: Page, live_server_url: str
):
    """Risk: `portfolio_name` is free text. A wallet called "Główny / IKZE: 2026" would
    build a name Windows refuses to save, and one made only of emoji would sanitise to
    nothing and yield "_2026-07-30.csv". Exercised through the real helper, because
    that is the single place the rule lives."""
    _login(page, live_server_url)
    _open_portfolio_glowny(page)

    names = page.evaluate(
        """() => ({
             illegal: _csvFilename('Główny / IKZE: 2026', '2026-07-30'),
             emoji: _csvFilename('🙂🙂', '2026-07-30'),
             diacritics: _csvFilename('Łódź Ćma', '2026-07-30'),
           })"""
    )

    assert not re.search(r'[\\/:*?"<>|]', names["illegal"]), names["illegal"]
    assert names["illegal"].endswith("_2026-07-30.csv")
    assert not names["emoji"].startswith("_"), names["emoji"]
    assert names["diacritics"] == "Lodz_Cma_2026-07-30.csv"
