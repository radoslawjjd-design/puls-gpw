import re

from playwright.sync_api import Page, expect

from tests.e2e.conftest import e2e_login_email



def _login(page: Page, base_url: str) -> None:
    # PUL-74: widoki per-user są JWT-only — logowanie przez formularz e-mail.
    e2e_login_email(page, base_url)


def _open_portfolio(page: Page) -> None:
    page.get_by_role("button", name="Mój portfel").click()
    # PUL-90: default tab is read-only "Wszystkie" — select Główny for the editable view.
    page.locator("#pp-portfolio-tabs .pp-portfolio-tab", has_text="Główny").click()


def _add_position(page: Page, ticker: str, company: str, shares: str, price: str) -> None:
    pp = page.locator("#portfolio-positions-view")
    pp.get_by_role("button", name="Dodaj pozycję").click()
    pp.get_by_placeholder("Ticker (np. PKO)").fill(ticker)
    pp.get_by_placeholder("Nazwa spółki").fill(company)
    pp.get_by_placeholder("Ilość akcji").fill(shares)
    pp.get_by_placeholder("Śr. cena zakupu (PLN)").fill(price)
    pp.get_by_role("button", name="Dodaj", exact=True).click()


def test_user_can_add_position_and_see_it_in_table(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_portfolio(page)
    _add_position(page, "PKO", "PKO BP SA", "10", "40.00")

    expect(page.locator("#pp-tbody")).to_contain_text("PKO")
    expect(page.locator("#pp-tbody")).to_contain_text("10")


def test_user_can_edit_position_and_see_updated_values(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_portfolio(page)
    _add_position(page, "PKO", "PKO BP SA", "10", "40.00")

    expect(page.locator("#pp-tbody")).to_contain_text("PKO")

    page.locator("#pp-tbody tr", has_text="PKO").get_by_role("button", name="Edytuj").click()

    expect(page.locator("#pp-edit-overlay")).to_be_visible()
    expect(page.locator("#pp-edit-title")).to_contain_text("PKO")
    expect(page.locator("#pp-edit-shares")).to_have_value("10")
    expect(page.locator("#pp-edit-price")).to_have_value("40")

    page.locator("#pp-edit-shares").fill("20")
    page.locator("#pp-edit-save-btn").click()

    expect(page.locator("#pp-tbody")).to_contain_text("20")


def test_user_can_delete_position_with_confirmation(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_portfolio(page)
    _add_position(page, "PKO", "PKO BP SA", "10", "40.00")

    expect(page.locator("#pp-tbody")).to_contain_text("PKO")

    page.on("dialog", lambda d: d.accept())
    page.locator("#pp-tbody tr", has_text="PKO").get_by_role("button", name="Usuń").click()

    expect(page.locator("#pp-tbody")).not_to_contain_text("PKO")


def test_positions_show_dashes_when_no_price_data(page: Page, live_server_url: str):
    _login(page, live_server_url)
    _open_portfolio(page)

    # CDR from _FAKE_PORTFOLIO_POSITIONS has current_price=None — all price columns show "—"
    expect(page.locator("#pp-tbody")).to_contain_text("CDR")
    expect(page.locator("#pp-tbody")).to_contain_text("—")


# ── PUL-123: the daily-change cell carries its direction ─────────────────────
#
# Four states, and the pairs that must not collapse: zero is not "unknown", and
# zero is not "a loss". Colours are read as computed rgb() and compared by
# channel rather than against hex literals — the claim is "up looks like a gain
# and down looks like a loss", which has to survive a palette change. The cells
# are addressed by `data-label`, the same anchor the mobile-layout tests use;
# there is no role that names a single column's cell.

_DAILY_CELL = 'td[data-label="Zmiana dzienna"]'


def _daily_cell(page: Page, ticker: str):
    return page.locator("#pp-tbody tr", has_text=ticker).locator(_DAILY_CELL)


def _rgb(page: Page, ticker: str) -> tuple[float, float, float]:
    raw = _daily_cell(page, ticker).evaluate("e => getComputedStyle(e).color")
    r, g, b = (float(v) for v in raw.replace("rgba", "rgb").split("(")[1].split(")")[0].split(",")[:3])
    return r, g, b


def test_daily_change_shows_its_direction_in_text_and_colour(
    page: Page, live_server_url: str
):
    _login(page, live_server_url)
    _open_portfolio(page)
    expect(page.locator("#pp-tbody")).to_contain_text("PGE")

    # Text: the sign travels with the number, so colour is never the only signal.
    expect(_daily_cell(page, "PKO")).to_have_text("+1.50%")
    expect(_daily_cell(page, "PGE")).to_have_text("-2.40%")
    expect(_daily_cell(page, "LPP")).to_have_text("0.00%")
    expect(_daily_cell(page, "CDR")).to_have_text("—")

    up, down, flat, unknown = (_rgb(page, t) for t in ("PKO", "PGE", "LPP", "CDR"))

    assert up[1] > up[0], f"a gain should read green, got rgb{up}"
    assert down[0] > down[1], f"a loss should read red, got rgb{down}"
    assert up != flat and down != flat, f"flat {flat} is tinted like up {up} / down {down}"
    assert flat == unknown, f"zero {flat} and unknown {unknown} should share the default colour"


def test_a_move_too_small_to_print_is_not_coloured_as_a_gain(
    page: Page, live_server_url: str
):
    """Two decimals is the cell's resolution, so it is also the contract. A
    +0.004% day prints as 0.00% — colouring it green would leave the cell
    claiming "flat" and "up" at once."""
    _login(page, live_server_url)
    _open_portfolio(page)
    expect(page.locator("#pp-tbody")).to_contain_text("OPL")

    expect(_daily_cell(page, "OPL")).to_have_text("0.00%")
    assert _rgb(page, "OPL") == _rgb(page, "LPP"), (
        "a move that prints as 0.00% must look the same as a true flat day"
    )


def test_daily_change_colour_survives_dark_mode_and_a_phone(
    page: Page, live_server_url: str
):
    """The card layout at ≤640px restyles the same <td>, and dark mode overrides
    the same two classes — so both are regressions in one place, not two."""
    page.set_viewport_size({"width": 360, "height": 740})
    _login(page, live_server_url)
    _open_portfolio(page)
    expect(page.locator("#pp-tbody")).to_contain_text("PGE")

    for theme in ("light", "dark"):
        page.evaluate("t => { localStorage.setItem('faro_theme', t); _applyTheme(t); }", theme)
        up, down, flat = (_rgb(page, t) for t in ("PKO", "PGE", "LPP"))
        assert up[1] > up[0], f"{theme}: a gain should read green, got rgb{up}"
        assert down[0] > down[1], f"{theme}: a loss should read red, got rgb{down}"
        assert up != flat and down != flat, f"{theme}: flat {flat} is tinted"


def test_wszystkie_aggregate_view_is_default_and_read_only(page: Page, live_server_url: str):
    """PUL-90: 'Wszystkie' is the first + default tab and shows positions in a read-only
    aggregate (no per-row edit/delete, no 'Dodaj pozycję'); selecting a wallet restores
    editing and scopes back to that wallet."""
    _login(page, live_server_url)
    page.get_by_role("button", name="Mój portfel").click()

    tabs = page.locator("#pp-portfolio-tabs")
    first_tab = tabs.locator(".pp-portfolio-tab").first
    expect(first_tab).to_have_text("Wszystkie")
    # default on entry: the aggregate tab is the active one
    expect(page.locator(".pp-portfolio-tab.active")).to_have_text("Wszystkie")

    # aggregate positions render + summary visible
    expect(page.locator("#pp-tbody")).to_contain_text("PKO")
    expect(page.locator("#pp-summary")).to_be_visible()

    # read-only: no per-row edit/delete controls, no "Dodaj pozycję"
    expect(page.locator("#pp-tbody button", has_text="Edytuj")).to_have_count(0)
    expect(page.locator("#pp-tbody button", has_text="Usuń")).to_have_count(0)
    expect(page.locator("#pp-add-toggle-btn")).to_be_hidden()

    # selecting the Główny wallet restores editing and the add-position toggle
    tabs.locator(".pp-portfolio-tab", has_text="Główny").click()
    expect(page.locator("#pp-tbody button", has_text="Edytuj").first).to_be_visible()
    expect(page.locator("#pp-add-toggle-btn")).to_be_visible()


# ── PUL-123 part 2: how long each position has been held ─────────────────────
#
# The data is PUL-114's — `first_buy_date` is the oldest still-OPEN FIFO lot, so
# a ticker sold to zero and re-bought dates from the re-buy. This suite only
# checks the rendering, and the case worth the most is the last one: absence must
# read as absence. "0 dni" would be a lie about a position nobody dated.

_HELD_CELL = 'td[data-label="Okres posiadania"]'


def _held_cell(page: Page, ticker: str):
    return page.locator("#pp-tbody tr", has_text=ticker).locator(_HELD_CELL)


def test_holding_period_switches_units_instead_of_printing_raw_days(
    page: Page, live_server_url: str
):
    """Days stay days while they are readable; past a quarter they become months
    and years. Nobody reads "800 dni" as two years and two months."""
    _login(page, live_server_url)
    _open_portfolio(page)
    expect(page.locator("#pp-tbody")).to_contain_text("PGE")

    expect(_held_cell(page, "PKO")).to_have_text("2 lata 2 mies.")   # 800 days
    expect(_held_cell(page, "PGE")).to_have_text("3 mies.")          # 100 days
    expect(_held_cell(page, "CDR")).to_have_text("15 dni")           # 15 days


def test_a_position_with_no_acquisition_date_shows_no_holding_period(
    page: Page, live_server_url: str
):
    """A hand-entered position has no operations behind it, so there is no date
    to report. PUL-123 names this explicitly: it must not become "0 dni", which
    would claim the shares were bought today."""
    _login(page, live_server_url)
    _open_portfolio(page)
    expect(page.locator("#pp-tbody")).to_contain_text("LPP")

    cell = _held_cell(page, "LPP")
    expect(cell).to_have_text("—")
    expect(cell).not_to_have_text(re.compile(r"\d"))


def test_holding_period_sorts_by_date_not_by_the_text_it_prints(
    page: Page, live_server_url: str
):
    """The cell renders in two different formats, so sorting its text would put
    "15 dni" above "3 mies.". The header sorts on the raw ISO date instead, and
    the undated position goes last in both directions rather than counting as
    the oldest."""
    _login(page, live_server_url)
    _open_portfolio(page)
    expect(page.locator("#pp-tbody")).to_contain_text("PKO")

    header = page.locator('#pp-thead th[data-key="first_buy_date"]')
    header.click()
    oldest_first = page.locator("#pp-tbody tr td:first-child").all_inner_texts()
    assert oldest_first.index("PKO") < oldest_first.index("PGE") < oldest_first.index("CDR")

    header.click()
    newest_first = page.locator("#pp-tbody tr td:first-child").all_inner_texts()
    assert newest_first.index("CDR") < newest_first.index("PGE") < newest_first.index("PKO")
    assert newest_first[-1] == "LPP", "an undated position is not the newest either"


def test_holding_period_survives_the_phone_card_layout(
    page: Page, live_server_url: str
):
    """The ≤640px card layout labels cells positionally
    (`#pp-tbody td:nth-child(n+3):not(:last-child)::before`), not by name — so a
    column inserted in the middle either inherits the mechanism or silently loses
    its label. Part 1 got this for free and assumed rather than checked it."""
    page.set_viewport_size({"width": 375, "height": 812})
    _login(page, live_server_url)
    _open_portfolio(page)
    expect(page.locator("#pp-tbody")).to_contain_text("PKO")

    cell = _held_cell(page, "PKO")
    expect(cell).to_be_visible()
    expect(cell).to_have_text("2 lata 2 mies.")
    label = cell.evaluate("e => getComputedStyle(e, '::before').content")
    assert "Okres posiadania" in label, f"the card layout dropped the label: {label}"


def test_the_holding_period_never_reads_shorter_as_a_position_gets_older(
    page: Page, live_server_url: str
):
    """The unit switches at 90 days, and that is where the formatter can lie. 89
    days is ~2.9 months, so flooring made the cell read "89 dni" one day and
    "2 mies." the next — the displayed age going *backwards* while the position
    aged. Checked against the function itself rather than through a fixture,
    because pinning a boundary needs day-by-day resolution."""
    _login(page, live_server_url)
    _open_portfolio(page)

    texts = page.evaluate(
        "() => [88, 89, 90, 91, 92, 120, 364, 365, 424].map(d => _holdingText(d))"
    )
    assert texts[:3] == ["88 dni", "89 dni", "3 mies."], texts
    # Months must never decrease across the switch, nor within the months branch.
    months = [int(t.split()[0]) for t in texts[2:6]]
    assert months == sorted(months), f"the reported age went backwards: {texts}"
    assert texts[-1] == "1 rok 2 mies.", "424 days is a year and ~1.9 months"
