from unittest.mock import MagicMock, patch

from src.company_profile import extract_company_profile_links, fetch_company_profile
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
