"""E2E tests — wallet management UX (PUL-95 follow-ups).

Three separate reports, one file: the delete reported failure for a delete that
had in fact gone through, the add-portfolio dropdown kept offering a type the
user already owned (guaranteed 409), and slow writes gave no sign they were
running so the only way to tell a working button from a dead one was to click
again.

The wallet fakes in the shared conftest are static by design, so every test here
routes the two calls it cares about itself rather than teaching the shared store
to mutate — a stateful wallet store would leak across the whole suite (the PUL-90
lesson about adding entities to a shared conftest).
"""
import json
import re

from playwright.sync_api import Page, expect

from tests.e2e.conftest import _FAKE_PORTFOLIO_ID, e2e_login_email

# Two `*` on purpose: a single one does not cross a slash, so
# `.../wallets*` silently misses the DELETE at `.../wallets/<id>` and lets it
# through to the real server — the test then passes for the wrong reason.
_WALLETS_URL = "**/api/portfolio/wallets**"

_GLOWNY = {
    "portfolio_id": _FAKE_PORTFOLIO_ID,
    "portfolio_type": "glowny",
    "portfolio_name": None,
    "display_order": 1,
    "user_id": "test-client-id",
    "created_at": "2026-01-01T00:00:00+00:00",
}
_IKZE = {
    "portfolio_id": "wallet-ikze-002",
    "portfolio_type": "ikze",
    "portfolio_name": None,
    "display_order": 2,
    "user_id": "test-client-id",
    "created_at": "2026-01-02T00:00:00+00:00",
}


def _open_wallet(page: Page, base_url: str) -> None:
    e2e_login_email(page, base_url)
    page.get_by_role("button", name="Mój portfel").click()
    expect(page.locator("#pp-portfolio-tabs")).to_contain_text("Główny")


def _route_wallets(page: Page, before: list[dict], after: list[dict], delete_status: int):
    """Serve the wallet list, switching to `after` once the DELETE has been answered.

    Returns the mutable state dict so a test can assert the DELETE was actually
    issued rather than inferring it from the UI.
    """
    state = {"deleted": False}

    def handler(route):
        request = route.request
        if request.method == "DELETE":
            state["deleted"] = True
            route.fulfill(status=delete_status, body="")
            return
        body = after if state["deleted"] and delete_status < 300 else before
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)
        )

    page.route(_WALLETS_URL, handler)
    return state


def test_deleting_a_wallet_reports_success_not_failure(page: Page, live_server_url: str):
    """Risk: the delete goes through and the UI says it failed.

    That was the reported bug — a scary popup over a completed destructive action,
    with the wallet actually gone after a reload. Success is now decided by
    re-reading the wallet list, so this asserts the message, not the code path.
    """
    _open_wallet(page, live_server_url)
    state = _route_wallets(page, [_GLOWNY, _IKZE], [_GLOWNY], delete_status=204)
    page.on("dialog", lambda d: d.accept())

    try:
        # Force the tabs to re-render from the routed list so IKZE exists to delete.
        page.evaluate("() => { _portfoliosFetched = false; return fetchUserPortfolios(); }")
        expect(page.locator("#pp-portfolio-tabs")).to_contain_text("IKZE")

        page.locator(
            f'#pp-portfolio-tabs .pp-portfolio-tab[data-portfolio-id="{_IKZE["portfolio_id"]}"] '
            ".pp-tab-del-icon"
        ).click()

        expect(page.locator("#toast")).to_have_class(re.compile(r"toast-show"))
        expect(page.locator("#toast")).to_contain_text("Usunięto portfel")
        expect(page.locator("#pp-portfolio-tabs")).not_to_contain_text("IKZE")
    finally:
        page.unroute_all(behavior="ignoreErrors")

    assert state["deleted"], "the DELETE was never issued"


def test_a_wallet_that_survives_the_delete_is_reported_as_failed(
    page: Page, live_server_url: str
):
    """The mirror image: silence would be just as wrong as a false alarm.

    Break-verification for the test above — if the UI reported success
    unconditionally, that test would pass and this one would fail.
    """
    _open_wallet(page, live_server_url)
    _route_wallets(page, [_GLOWNY, _IKZE], [_GLOWNY], delete_status=500)
    page.on("dialog", lambda d: d.accept())

    try:
        page.evaluate("() => { _portfoliosFetched = false; return fetchUserPortfolios(); }")
        expect(page.locator("#pp-portfolio-tabs")).to_contain_text("IKZE")

        page.locator(
            f'#pp-portfolio-tabs .pp-portfolio-tab[data-portfolio-id="{_IKZE["portfolio_id"]}"] '
            ".pp-tab-del-icon"
        ).click()

        expect(page.locator("#toast")).to_contain_text("Nie udało się usunąć")
        expect(page.locator("#pp-portfolio-tabs")).to_contain_text("IKZE")
    finally:
        page.unroute_all(behavior="ignoreErrors")


def test_add_portfolio_does_not_offer_a_type_the_user_already_has(
    page: Page, live_server_url: str
):
    """Risk: offering a taken type is a guaranteed 409 the user cannot act on.

    The fake user owns Główny, so that option must be out and the preselection
    must land on the first type that is actually free.
    """
    _open_wallet(page, live_server_url)

    page.locator("#pp-add-portfolio-btn").click()
    expect(page.locator("#pp-add-portfolio-overlay")).to_be_visible()

    glowny = page.locator("#pp-portfolio-type-select option[value='glowny']")
    assert glowny.get_attribute("disabled") is not None
    assert glowny.get_attribute("hidden") is not None
    # IKZE is free, so it is both selectable and the new default.
    ikze = page.locator("#pp-portfolio-type-select option[value='ikze']")
    assert ikze.get_attribute("disabled") is None
    expect(page.locator("#pp-portfolio-type-select")).to_have_value("ikze")


def test_two_inny_wallets_close_the_inny_option(page: Page, live_server_url: str):
    """The cap on "Inny" is two, not one — the dropdown has to encode that
    difference or it either blocks a legal wallet or offers an illegal one."""
    _open_wallet(page, live_server_url)

    def seed(count):
        page.evaluate(
            """(n) => {
                _ppPortfolios = [{portfolio_id: 'g', portfolio_type: 'glowny'}].concat(
                  Array.from({length: n}, (_, i) => (
                    {portfolio_id: 'i' + i, portfolio_type: 'inny', portfolio_name: 'X' + i}))
                );
                _openAddPortfolioModal();
            }""",
            count,
        )

    seed(1)
    inny = page.locator("#pp-portfolio-type-select option[value='inny']")
    assert inny.get_attribute("disabled") is None, "one Inny wallet must leave a second available"

    seed(2)
    assert inny.get_attribute("disabled") is not None, "the second Inny is the cap"


def test_a_slow_write_shows_a_spinner_on_the_button(page: Page, live_server_url: str):
    """Risk: BigQuery writes take seconds and the button looked dead while they ran.

    Held mid-flight on purpose: the spinner only exists between the click and the
    response, so asserting after the request settles would pass no matter what.
    """
    _open_wallet(page, live_server_url)
    held = {"done": False}

    def hold(route):
        # First matching request only. A handler that sleeps on every match is
        # still armed at teardown, and sleeping on a closing page poisons the
        # next test's setup (the lesson from the import-flash test).
        if route.request.method == "POST" and not held["done"]:
            held["done"] = True
            page.wait_for_timeout(2000)
        route.continue_()

    try:
        page.route(_WALLETS_URL, hold)
        page.locator("#pp-add-portfolio-btn").click()
        page.locator("#pp-portfolio-type-select").select_option("ikze")
        save = page.locator("#pp-portfolio-modal-save")
        save.click()

        expect(save.locator(".spinner")).to_be_visible()
        expect(save).to_have_attribute("aria-busy", "true")
        expect(save).to_be_disabled()
    finally:
        page.unroute_all(behavior="ignoreErrors")

    # And it goes away again — a spinner that never clears is its own bug.
    expect(page.locator("#pp-portfolio-modal-save .spinner")).to_have_count(0)
