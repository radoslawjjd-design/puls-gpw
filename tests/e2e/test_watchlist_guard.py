"""E2E — pul-60 Phase 6: _watchlistFetched guard fires only once on repeated navigation.

Risk: repeated navigation to Obserwowane must NOT re-fetch GET /watchlist on
the 2nd and 3rd visit. Without the guard every tab switch causes an extra
round-trip (degrading perceived performance). The guard is pure JS state —
not provable by a unit or integration test alone.

Seed: tests/e2e/test_my_wallet.py
"""
from playwright.sync_api import Page, expect

from tests.e2e.conftest import e2e_login_email




def _login(page: Page, base_url: str) -> None:
    # PUL-74: widoki per-user są JWT-only — logowanie przez formularz e-mail.
    e2e_login_email(page, base_url)


def test_watchlist_guard_fires_only_once_on_repeated_navigation(
    page: Page, live_server_url: str
) -> None:
    """Risk (pul-60): _watchlistFetched guard prevents repeated GET /watchlist
    on every Obserwowane tab visit. 3× navigation must produce exactly 1 call."""
    watchlist_calls: list[str] = []
    page.on(
        "request",
        lambda req: watchlist_calls.append(req.url)
        if req.url.split("?")[0].endswith("/watchlist") and req.method == "GET"
        else None,
    )

    _login(page, live_server_url)

    # Visit 1 — should trigger exactly one GET /watchlist
    page.get_by_role("button", name="Obserwowane").click()
    expect(page.locator("#my-wallet-view")).to_be_visible()
    page.wait_for_load_state("networkidle")

    # Navigate away
    page.get_by_role("button", name="Mój portfel").click()
    expect(page.locator("#portfolio-positions-view")).to_be_visible()

    # Visit 2 — guard must suppress the re-fetch
    page.get_by_role("button", name="Obserwowane").click()
    expect(page.locator("#my-wallet-view")).to_be_visible()

    # Navigate away again
    page.get_by_role("button", name="Mój portfel").click()
    expect(page.locator("#portfolio-positions-view")).to_be_visible()

    # Visit 3 — guard must suppress the re-fetch again
    page.get_by_role("button", name="Obserwowane").click()
    expect(page.locator("#my-wallet-view")).to_be_visible()
    page.wait_for_load_state("networkidle")

    assert len(watchlist_calls) == 1, (
        f"Expected 1 GET /watchlist (guard active) but got {len(watchlist_calls)}. "
        "_watchlistFetched guard is not working."
    )
