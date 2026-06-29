"""Unit tests for src/gpw_etf_metrics.py (PUL-67)."""
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

_SNAPSHOT_DATE = date(2026, 6, 29)
_FETCHED_AT = datetime(2026, 6, 29, 10, 0, 0, tzinfo=timezone.utc)

# Minimal GPW ETF page fixture matching the REAL page structure:
# - Tables have class="table etf-footable"
# - Price cells use CSS classes (_rel, _open, _min, _max, _last, change, _c_vol)
#   with data-o-value attributes containing clean floats
# - Sections identified by preceding <h3> tag
_GPW_ETF_HTML = """
<html><body>
<h3>ETF</h3>
<table class="table etf-footable">
 <thead><tr class="footable-header-row">
  <th></th><th>Instrument</th><th>ISIN</th><th>Waluta</th><th>NAV*</th>
  <th class="_rel">Kurs odn.</th><th class="_open">Kurs otw.</th>
  <th class="_min">Kurs min.</th><th class="_max">Kurs maks.</th>
  <th class="_last">Kurs ost. trans./zamk.</th><th>Flaga</th>
  <th class="change">Zm.do k.odn. (%)</th><th class="_c_vol">Wol. - obr. skumul</th>
 </tr></thead>
 <tbody>
  <tr>
   <td></td>
   <td class="left nowrap col">ETFBW20TR</td>
   <td class="left nowrap col">PLBTETF00015</td>
   <td>PLN</td><td>72,97</td>
   <td class="right _rel col" data-o-value="72.990000">72,9900</td>
   <td class="right _open col" data-o-value="73.100000">73,1000</td>
   <td class="right _min col" data-o-value="72.500000">72,5000</td>
   <td class="right _max col" data-o-value="73.200000">73,2000</td>
   <td class="right _last col" data-o-value="72.810000">72,8100</td>
   <td></td>
   <td class="right change col" data-o-value="-0.250000">-0,25</td>
   <td class="right _c_vol col" data-o-value="29602">29 602</td>
  </tr>
  <tr>
   <td></td>
   <td class="left nowrap col">ETFHANESGO</td>
   <td class="left nowrap col">IE00BNTVVR89</td>
   <td>PLN</td><td>0,00</td>
   <td class="right _rel col" data-o-value="62.000000">62,0000</td>
   <td class="right _open col">—</td>
   <td class="right _min col">—</td>
   <td class="right _max col">—</td>
   <td class="right _last col">—</td>
   <td></td>
   <td class="right change col">—</td>
   <td class="right _c_vol col">—</td>
  </tr>
 </tbody>
</table>
<h3>ETC</h3>
<table class="table etf-footable">
 <thead><tr class="footable-header-row">
  <th></th><th>Instrument</th><th>ISIN</th><th>Waluta</th><th>NAV*</th>
  <th class="_rel">Kurs odn.</th><th class="_open">Kurs otw.</th>
  <th class="_min">Kurs min.</th><th class="_max">Kurs maks.</th>
  <th class="_last">Kurs ost. trans./zamk.</th><th>Flaga</th>
  <th class="change">Zm.do k.odn. (%)</th><th class="_c_vol">Wol. - obr. skumul</th>
 </tr></thead>
 <tbody>
  <tr>
   <td></td>
   <td class="left nowrap col">ETCGLDRMAU</td>
   <td class="left nowrap col">XS2115336336</td>
   <td>PLN</td><td>0,00</td>
   <td class="right _rel col" data-o-value="151.660000">151,6600</td>
   <td class="right _open col" data-o-value="151.000000">151,0000</td>
   <td class="right _min col" data-o-value="149.500000">149,5000</td>
   <td class="right _max col" data-o-value="152.000000">152,0000</td>
   <td class="right _last col" data-o-value="149.900000">149,9000</td>
   <td></td>
   <td class="right change col" data-o-value="-1.160000">-1,16</td>
   <td class="right _c_vol col" data-o-value="1638">1 638</td>
  </tr>
 </tbody>
</table>
<h3>ETN</h3>
<table class="table etf-footable">
 <thead><tr class="footable-header-row">
  <th></th><th>Instrument</th><th>ISIN</th><th>Waluta</th><th>NAV*</th>
  <th class="_rel">Kurs odn.</th><th class="_open">Kurs otw.</th>
  <th class="_min">Kurs min.</th><th class="_max">Kurs maks.</th>
  <th class="_last">Kurs ost. trans./zamk.</th><th>Flaga</th>
  <th class="change">Zm.do k.odn. (%)</th><th class="_c_vol">Wol. - obr. skumul</th>
 </tr></thead>
 <tbody>
  <tr>
   <td></td>
   <td class="left nowrap col">ETNVIRBTCP</td>
   <td class="left nowrap col">SE0027598038</td>
   <td>PLN</td><td>0,00</td>
   <td class="right _rel col" data-o-value="22.440000">22,4400</td>
   <td class="right _open col" data-o-value="22.400000">22,4000</td>
   <td class="right _min col" data-o-value="22.300000">22,3000</td>
   <td class="right _max col" data-o-value="22.600000">22,6000</td>
   <td class="right _last col" data-o-value="22.490000">22,4900</td>
   <td></td>
   <td class="right change col" data-o-value="0.220000">0,22</td>
   <td class="right _c_vol col" data-o-value="921">921</td>
  </tr>
 </tbody>
</table>
</body></html>
"""


def _mock_resp(html: str) -> MagicMock:
    m = MagicMock()
    m.text = html
    return m


# ── Phase 2.A: happy path ────────────────────────────────────────────────────

def test_fetch_etf_page_returns_all_instruments():
    """fetch_etf_page must return one instruments entry per instrument (4 in fixture)."""
    from src.gpw_etf_metrics import fetch_etf_page

    with patch("src.gpw_etf_metrics.get", return_value=_mock_resp(_GPW_ETF_HTML)):
        instruments, quotes = fetch_etf_page(_SNAPSHOT_DATE, _FETCHED_AT)

    assert len(instruments) == 4
    assert len(quotes) == 4


def test_fetch_etf_page_parses_etf_price_correctly():
    """fetch_etf_page must parse kurs_zamkniecia and zmiana_procentowa for ETFBW20TR."""
    from src.gpw_etf_metrics import fetch_etf_page

    with patch("src.gpw_etf_metrics.get", return_value=_mock_resp(_GPW_ETF_HTML)):
        instruments, quotes = fetch_etf_page(_SNAPSHOT_DATE, _FETCHED_AT)

    quote = next(q for q in quotes if q["ticker"] == "ETFBW20TR")
    assert quote["kurs_zamkniecia"] == pytest.approx(72.81)
    assert quote["zmiana_procentowa"] == pytest.approx(-0.25)
    assert quote["snapshot_date"] == "2026-06-29"


# ── Phase 2.B: dash → None ────────────────────────────────────────────────────

def test_fetch_etf_page_dash_becomes_none():
    """fetch_etf_page must parse '—' as None for kurs_zamkniecia and other price fields."""
    from src.gpw_etf_metrics import fetch_etf_page

    with patch("src.gpw_etf_metrics.get", return_value=_mock_resp(_GPW_ETF_HTML)):
        _, quotes = fetch_etf_page(_SNAPSHOT_DATE, _FETCHED_AT)

    quote = next(q for q in quotes if q["ticker"] == "ETFHANESGO")
    assert quote["kurs_zamkniecia"] is None
    assert quote["zmiana_procentowa"] is None
    assert quote["zmiana_kwotowa"] is None  # cannot derive if both are None


# ── Phase 2.C: zmiana_kwotowa derivation ─────────────────────────────────────

def test_fetch_etf_page_derives_zmiana_kwotowa():
    """fetch_etf_page must compute zmiana_kwotowa = kurs_odn * zmiana_procentowa / 100."""
    from src.gpw_etf_metrics import fetch_etf_page

    with patch("src.gpw_etf_metrics.get", return_value=_mock_resp(_GPW_ETF_HTML)):
        _, quotes = fetch_etf_page(_SNAPSHOT_DATE, _FETCHED_AT)

    quote = next(q for q in quotes if q["ticker"] == "ETFBW20TR")
    # kurs_odn=72.99, zmiana_procentowa=-0.25 → zmiana_kwotowa ≈ -0.1825
    expected = 72.99 * (-0.25) / 100
    assert quote["zmiana_kwotowa"] == pytest.approx(expected, rel=1e-3)


# ── Phase 2.D: instrument_type from section heading ──────────────────────────

def test_fetch_etf_page_assigns_correct_instrument_types():
    """fetch_etf_page must assign ETF/ETC/ETN type from the preceding section heading."""
    from src.gpw_etf_metrics import fetch_etf_page

    with patch("src.gpw_etf_metrics.get", return_value=_mock_resp(_GPW_ETF_HTML)):
        instruments, _ = fetch_etf_page(_SNAPSHOT_DATE, _FETCHED_AT)

    assert instruments["ETFBW20TR"]["instrument_type"] == "ETF"
    assert instruments["ETCGLDRMAU"]["instrument_type"] == "ETC"
    assert instruments["ETNVIRBTCP"]["instrument_type"] == "ETN"
