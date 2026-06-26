"""Unit tests for src/bankier_metrics.py."""
from unittest.mock import MagicMock, patch

import pytest

from src.bankier_metrics import fetch_listing_page, symbol_from_hop_url
from src.exceptions import ScraperError

_HOP_URL_ECHO = "https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=ECHO"
_HOP_URL_MOL = "https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=MOL"
_HOP_URL_NO_SYMBOL = "https://www.bankier.pl/inwestowanie/profile/quote.html"
_HOP_URL_EMPTY_SYMBOL = "https://www.bankier.pl/inwestowanie/profile/quote.html?symbol="

_LISTING_HTML = """
<html><body>
<table class="m-quotes-data-table">
 <thead><tr><th>Spółka</th><th>Kurs</th><th>Zm. %</th><th>Zm.</th>
             <th>L. transakcji</th><th>Obrót (zł)</th><th>Otwarcie</th><th>Max</th><th>Min</th><th>Czas</th></tr></thead>
 <tbody>
  <tr>
   <td><a href="/inwestowanie/profile/quote.html?symbol=PKO">PKO BP</a></td>
   <td>103,62</td><td>-0,56%</td><td>-0,58</td>
   <td>5 000</td><td>51 810 000,00</td><td>104,00</td><td>104,20</td><td>103,40</td>
   <td>2026-06-26 17:04</td>
  </tr>
  <tr>
   <td><a href="/inwestowanie/profile/quote.html?symbol=CDR">CD Projekt</a></td>
   <td>217,40</td><td>-2,69%</td><td>-6,00</td>
   <td>1 200</td><td>261 000,00</td><td>220,00</td><td>221,00</td><td>216,00</td>
   <td>2026-06-26 17:04</td>
  </tr>
 </tbody>
</table>
</body></html>
"""

_LISTING_HTML_NO_TABLE = "<html><body><p>no table</p></body></html>"


def _mock_resp(html: str) -> MagicMock:
    m = MagicMock()
    m.text = html
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


# --- fetch_listing_page ---

def test_fetch_listing_page_happy_path():
    with patch("src.bankier_metrics.get", return_value=_mock_resp(_LISTING_HTML)):
        result = fetch_listing_page("akcje")

    assert "PKO" in result
    assert "CDR" in result
    assert result["PKO"]["kurs_zamkniecia"] == pytest.approx(103.62)
    assert result["PKO"]["zmiana_procentowa"] == pytest.approx(-0.56)
    assert result["PKO"]["zmiana_kwotowa"] == pytest.approx(-0.58)
    assert result["PKO"]["liczba_transakcji"] == 5000
    assert result["PKO"]["wartosc_obrotu"] == pytest.approx(51_810_000.0)
    assert result["PKO"]["kurs_otwarcia"] == pytest.approx(104.0)
    assert result["PKO"]["kurs_max"] == pytest.approx(104.2)
    assert result["PKO"]["kurs_min"] == pytest.approx(103.4)


def test_fetch_listing_page_returns_all_expected_keys():
    with patch("src.bankier_metrics.get", return_value=_mock_resp(_LISTING_HTML)):
        result = fetch_listing_page("akcje")

    expected = {
        "kurs_zamkniecia", "zmiana_procentowa", "zmiana_kwotowa",
        "kurs_otwarcia", "kurs_min", "kurs_max", "wartosc_obrotu", "liczba_transakcji",
    }
    assert set(result["PKO"].keys()) == expected


def test_fetch_listing_page_http_failure_returns_empty_dict():
    with patch("src.bankier_metrics.get", side_effect=ScraperError("timeout")):
        result = fetch_listing_page("akcje")

    assert result == {}


def test_fetch_listing_page_missing_table_returns_empty_dict():
    with patch("src.bankier_metrics.get", return_value=_mock_resp(_LISTING_HTML_NO_TABLE)):
        result = fetch_listing_page("akcje")

    assert result == {}


def test_fetch_listing_page_unknown_market_returns_empty_dict():
    result = fetch_listing_page("unknown-market")
    assert result == {}


def test_fetch_listing_page_new_connect():
    with patch("src.bankier_metrics.get", return_value=_mock_resp(_LISTING_HTML)):
        result = fetch_listing_page("new-connect")

    assert "PKO" in result
