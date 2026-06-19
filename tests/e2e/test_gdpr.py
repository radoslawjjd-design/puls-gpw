import pytest
from playwright.sync_api import Page, expect


@pytest.mark.gdpr
def test_gdpr_banner_lifecycle(page: Page, live_server_url: str):
    # Establish page context, then explicitly clear consent so initGdpr() fires
    page.goto(live_server_url)
    page.evaluate("localStorage.clear()")
    page.reload()

    # Banner must appear when consent is absent
    expect(page.locator("#gdpr-banner")).to_be_visible()

    # Clicking accept hides the banner
    page.locator("#gdpr-accept").click()
    expect(page.locator("#gdpr-banner")).to_be_hidden()

    # Reload — localStorage flag persists; banner must stay hidden
    page.reload()
    expect(page.locator("#gdpr-banner")).to_be_hidden()

    # Cleanup
    page.evaluate("localStorage.removeItem('gdpr_consent_v1')")
