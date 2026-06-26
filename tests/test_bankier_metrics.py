from unittest.mock import MagicMock, patch

import pytest

from src.bankier_metrics import fetch_daily_stats, fetch_profile_price, symbol_from_hop_url
from src.exceptions import ScraperError

_HOP_URL_ECHO = "https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=ECHO"
_HOP_URL_MOL = "https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=MOL"
_HOP_URL_NO_SYMBOL = "https://www.bankier.pl/inwestowanie/profile/quote.html"
_HOP_URL_EMPTY_SYMBOL = "https://www.bankier.pl/inwestowanie/profile/quote.html?symbol="

_ISIN_ECHO = "PLECHPS00019"
_SYMBOL_ECHO = "ECHO"

_API_RESPONSE = {
    "symbol": "ECHO",
    "isin": "PLECHPS00019",
    "profile_data": {
        "Kurs_odniesienia": 7.5,
        "Kurs_otwarcia": 7.6,
        "Minimum": 7.4,
        "Maximum": 7.85,
        "Wolumen_obrotu_szt": 12345,
        "Wartosc_obrotu_zl": 93000.0,
        "Liczba_transakcji": 200,
        "Stopa_zwrotu_1R": 15.3,
        "Kapitalizacja": 540000000.0,
        "Rynek": "GPW",
        "System_notowan": "WARSET",
    },
}


def _mock_resp(json_data: dict) -> MagicMock:
    m = MagicMock()
    m.json.return_value = json_data
    return m


# --- symbol_from_hop_url ---


def test_symbol_from_hop_url_extracts_symbol():
    assert symbol_from_hop_url(_HOP_URL_ECHO) == "ECHO"


def test_symbol_from_hop_url_extracts_mol():
    assert symbol_from_hop_url(_HOP_URL_MOL) == "MOL"


def test_symbol_from_hop_url_missing_param_returns_none():
    assert symbol_from_hop_url(_HOP_URL_NO_SYMBOL) is None


def test_symbol_from_hop_url_empty_param_returns_none():
    assert symbol_from_hop_url(_HOP_URL_EMPTY_SYMBOL) is None


def test_symbol_from_hop_url_malformed_url_returns_none():
    assert symbol_from_hop_url("not-a-url-at-all:::") is None


def test_symbol_from_hop_url_empty_string_returns_none():
    assert symbol_from_hop_url("") is None


# --- fetch_daily_stats ---


def test_fetch_daily_stats_happy_path():
    with patch("src.bankier_metrics.get", return_value=_mock_resp(_API_RESPONSE)):
        result = fetch_daily_stats(_ISIN_ECHO, _SYMBOL_ECHO)

    assert result is not None
    assert result["kurs_odniesienia"] == 7.5
    assert result["kurs_otwarcia"] == 7.6
    assert result["kurs_min"] == 7.4
    assert result["kurs_max"] == 7.85
    assert result["wolumen_obrotu"] == 12345
    assert result["wartosc_obrotu"] == 93000.0
    assert result["liczba_transakcji"] == 200
    assert result["stopa_zwrotu_1r"] == 15.3
    assert result["kapitalizacja"] == 540000000.0
    assert result["rynek"] == "GPW"
    assert result["system"] == "WARSET"


def test_fetch_daily_stats_returns_all_expected_keys():
    with patch("src.bankier_metrics.get", return_value=_mock_resp(_API_RESPONSE)):
        result = fetch_daily_stats(_ISIN_ECHO, _SYMBOL_ECHO)

    expected_keys = {
        "kurs_odniesienia", "kurs_otwarcia", "kurs_min", "kurs_max",
        "wolumen_obrotu", "wartosc_obrotu", "liczba_transakcji",
        "stopa_zwrotu_1r", "kapitalizacja", "rynek", "system",
    }
    assert set(result.keys()) == expected_keys


def test_fetch_daily_stats_scraper_error_returns_none():
    with patch("src.bankier_metrics.get", side_effect=ScraperError("timeout")):
        result = fetch_daily_stats(_ISIN_ECHO, _SYMBOL_ECHO)

    assert result is None


def test_fetch_daily_stats_json_decode_error_returns_none():
    m = MagicMock()
    m.json.side_effect = ValueError("bad json")
    with patch("src.bankier_metrics.get", return_value=m):
        result = fetch_daily_stats(_ISIN_ECHO, _SYMBOL_ECHO)

    assert result is None


def test_fetch_daily_stats_profile_data_missing_returns_none():
    with patch("src.bankier_metrics.get", return_value=_mock_resp({"symbol": "ECHO"})):
        result = fetch_daily_stats(_ISIN_ECHO, _SYMBOL_ECHO)

    assert result is None


def test_fetch_daily_stats_nullable_fields_return_none_when_absent():
    sparse = {"profile_data": {"Kurs_odniesienia": 8.0}}
    with patch("src.bankier_metrics.get", return_value=_mock_resp(sparse)):
        result = fetch_daily_stats(_ISIN_ECHO, _SYMBOL_ECHO)

    assert result is not None
    assert result["kurs_odniesienia"] == 8.0
    assert result["kurs_otwarcia"] is None
    assert result["rynek"] is None


# --- fetch_profile_price ---

_PROFILE_HTML = """
<html><body>
<div class="o-quotes-profile-header-box__numbers">
 <div class="o-quotes-profile-header-box__data">
  <div class="o-quotes-profile-header-box__price">
   <span class="a-quote-item -value">103,6200 zł</span>
  </div>
  <div class="o-quotes-profile-header-box__change">
   <span class="a-quote-item -percentage-change-with-arrow -negative">
    <span class="a-quote-item -percentage-change -negative">-0,56%</span>
   </span>
   <span class="a-quote-item -value-change -negative">-0,5800 zł</span>
  </div>
 </div>
</div>
</body></html>
"""

_PROFILE_HTML_POSITIVE = """
<html><body>
<div class="o-quotes-profile-header-box__numbers">
 <div class="o-quotes-profile-header-box__data">
  <div class="o-quotes-profile-header-box__price">
   <span class="a-quote-item -value">217,4000 zł</span>
  </div>
  <div class="o-quotes-profile-header-box__change">
   <span class="a-quote-item -percentage-change-with-arrow -positive">
    <span class="a-quote-item -percentage-change -positive">1,23%</span>
   </span>
   <span class="a-quote-item -value-change -positive">2,6400 zł</span>
  </div>
 </div>
</div>
</body></html>
"""


def _mock_html_resp(html: str) -> MagicMock:
    m = MagicMock()
    m.text = html
    return m


def test_fetch_profile_price_happy_path_negative():
    with patch("src.bankier_metrics.get", return_value=_mock_html_resp(_PROFILE_HTML)):
        result = fetch_profile_price("https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=PKO")

    assert result is not None
    assert result["kurs_zamkniecia"] == pytest.approx(103.62)
    assert result["zmiana_procentowa"] == pytest.approx(-0.56)
    assert result["zmiana_kwotowa"] == pytest.approx(-0.58)


def test_fetch_profile_price_happy_path_positive():
    with patch("src.bankier_metrics.get", return_value=_mock_html_resp(_PROFILE_HTML_POSITIVE)):
        result = fetch_profile_price("https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=CDR")

    assert result is not None
    assert result["kurs_zamkniecia"] == pytest.approx(217.4)
    assert result["zmiana_procentowa"] == pytest.approx(1.23)
    assert result["zmiana_kwotowa"] == pytest.approx(2.64)


def test_fetch_profile_price_http_failure_returns_none():
    with patch("src.bankier_metrics.get", side_effect=ScraperError("timeout")):
        result = fetch_profile_price("https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=PKO")

    assert result is None


def test_fetch_profile_price_missing_box_returns_none():
    html = "<html><body><p>no box here</p></body></html>"
    with patch("src.bankier_metrics.get", return_value=_mock_html_resp(html)):
        result = fetch_profile_price("https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=PKO")

    assert result is None


def test_fetch_profile_price_returns_none_fields_when_spans_absent():
    html = """<html><body>
    <div class="o-quotes-profile-header-box__numbers">
     <div class="o-quotes-profile-header-box__data"></div>
    </div></body></html>"""
    with patch("src.bankier_metrics.get", return_value=_mock_html_resp(html)):
        result = fetch_profile_price("https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=PKO")

    assert result == {"kurs_zamkniecia": None, "zmiana_procentowa": None, "zmiana_kwotowa": None}
