from unittest.mock import MagicMock, patch

from src.company_profile import extract_company_profile_links, fetch_company_profile, profile_url_for_ticker
from src.exceptions import ScraperError

_PROFILE_URL = "https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=TST"

_HTML_FULL_PROFILE = """\
<!DOCTYPE html><html><body>
<section id="quotes-profile-header-box" data-isin="PLTST0000011" data-symbol="TST">
<span class="a-heading__suffix -blue -with-dot">Test Company (TST)</span>
</section>
</body></html>"""

_HTML_MISSING_ISIN = """\
<!DOCTYPE html><html><body>
<span class="a-heading__suffix -blue -with-dot">Test Company (TST)</span>
</body></html>"""


def _mock_resp(html: str) -> MagicMock:
    m = MagicMock()
    m.text = html
    return m


def test_fetch_company_profile_happy_path():
    with patch("src.company_profile.get", return_value=_mock_resp(_HTML_FULL_PROFILE)):
        profile = fetch_company_profile(_PROFILE_URL)

    assert profile is not None
    assert profile.ticker == "TST"
    assert profile.company == "Test Company"
    assert profile.isin == "PLTST0000011"
    assert profile.hop_url == _PROFILE_URL


def test_fetch_company_profile_missing_isin():
    with patch("src.company_profile.get", return_value=_mock_resp(_HTML_MISSING_ISIN)):
        profile = fetch_company_profile(_PROFILE_URL)

    assert profile is not None
    assert profile.ticker == "TST"
    assert profile.company == "Test Company"
    assert profile.isin is None
    assert profile.hop_url == _PROFILE_URL


# ── PUL-102: the heading can carry more than one parenthesised group ─────────
#
# Bankier started rendering a brand or a status marker before the exchange
# abbreviation. Taking the first group made `Żabka` the company's identity across
# three tables, and put `$Zabka` on X. Both headings below are verbatim from live
# pages on 2026-07-30.
import pytest  # noqa: E402

from src.company_profile import _extract_heading  # noqa: E402


def _heading(text: str):
    from bs4 import BeautifulSoup

    html = f'<span class="a-heading__suffix -blue -with-dot">{text}</span>'
    return _extract_heading(BeautifulSoup(html, "html5lib"))


@pytest.mark.parametrize(
    "raw, ticker, company",
    [
        ("Zabka Group SA (Żabka) (ZAB)", "ZAB", "Zabka Group SA"),
        ("MegaPixel Studio SA (przejęty) (MPS)", "MPS", "MegaPixel Studio SA"),
        # Single-group control: the shape that has always worked must keep working.
        ("Alior Bank SA (ALR)", "ALR", "Alior Bank SA"),
        # A hyphen is legal — CREOTECH-PDA is a real bankier symbol for allotment
        # rights, correctly extracted today. Rejecting it would turn an unrelated
        # quirk into a new outage.
        ("Creotech Instruments SA (CREOTECH-PDA)", "CREOTECH-PDA", "Creotech Instruments SA"),
    ],
)
def test_the_ticker_comes_from_the_ticker_shaped_group(raw, ticker, company):
    assert _heading(raw) == (ticker, company)


def test_a_heading_with_no_ticker_shaped_group_yields_no_ticker():
    """A missing ticker is a gap the pipeline already handles; a wrong one is
    silent corruption that reaches three tables and a public tweet. The brand
    word must never be the fallback."""
    assert _heading("Jakaś Spółka SA (przejęty)") == (None, "Jakaś Spółka SA")


def test_a_heading_with_no_parentheses_yields_nothing():
    assert _heading("Spółka bez skrótu") == (None, None)


def test_fetch_company_profile_http_failure_returns_none():
    with patch("src.company_profile.get", side_effect=ScraperError("boom")):
        profile = fetch_company_profile(_PROFILE_URL)

    assert profile is None


_HTML_LISTING_PAGE = """\
<!DOCTYPE html><html><body>
<table>
<tr><td><a href="/inwestowanie/profile/quote.html?symbol=ECHO">Echo Investment</a></td></tr>
<tr><td><a href="/inwestowanie/profile/quote.html?symbol=MOL">Molecure</a></td></tr>
<tr><td><a href="/inwestowanie/profile/quote.html?symbol=ECHO">Echo Investment (duplicate row)</a></td></tr>
<tr><td><a href="/inwestowanie/notowania/akcje">Not a profile link</a></td></tr>
</table>
</body></html>"""


def test_extract_company_profile_links_dedupes_preserving_order():
    links = extract_company_profile_links(_HTML_LISTING_PAGE)

    assert links == [
        "https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=ECHO",
        "https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=MOL",
    ]


def test_extract_company_profile_links_empty_html_returns_empty_list():
    assert extract_company_profile_links("") == []


def test_extract_company_profile_links_absolute_href_passes_through_unchanged():
    html = """\
<!DOCTYPE html><html><body>
<a href="https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=ABS">Absolute</a>
</body></html>"""

    links = extract_company_profile_links(html)

    assert links == ["https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=ABS"]


def test_profile_url_for_ticker_builds_symbol_query_url():
    assert profile_url_for_ticker("PKP") == "https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=PKP"
