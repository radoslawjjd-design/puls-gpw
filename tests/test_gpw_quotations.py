"""Unit tests for src/gpw_quotations.py (PUL-98).

Fixtures mirror the REAL structure of both quotation tables, captured 2026-07-27:

- GPW main market: two-row <thead>; every column carries rowspan=2 except the two
  offer blocks, which carry colspan=3 and are detailed in the second row. Expanding
  colspan over the first row yields exactly 26 data columns.
- NewConnect: a single flat <thead> row, 20 data columns.

The two layouts differ in their identity block — GPW has `Op. na pap.` and no
`Segment`, NewConnect the reverse, so `Skrót` sits at data index 4 on GPW and 3 on
NewConnect. From index 6 they realign, which is exactly the kind of coincidence
that lets a positional parser look correct while reading the wrong column.

The close column is also labelled differently: `Kurs ost. trans. / zamk.` on GPW,
`Kurs ost. trans.` on NewConnect.
"""
from unittest.mock import MagicMock, patch

import pytest

from src.exceptions import ScraperError

# ── GPW main market: 22 <th> in row 0, two of them colspan=3 -> 26 data columns ──
_GPW_HTML = """
<html><body>
<table class="footable table">
 <thead>
  <tr>
   <th rowspan="2"></th><th rowspan="2">Op. na pap.</th><th rowspan="2">Nazwa</th>
   <th rowspan="2">ISIN</th><th rowspan="2">Skrót</th><th rowspan="2">Waluta</th>
   <th rowspan="2">Czas ost. trans.</th><th rowspan="2">Kurs odn.</th>
   <th rowspan="2">TKO</th><th rowspan="2">Kurs otw.</th><th rowspan="2">Kurs min.</th>
   <th rowspan="2">Kurs maks.</th><th rowspan="2">Kurs ost. trans. / zamk.</th>
   <th rowspan="2">Flaga</th><th rowspan="2">Zm.do k.odn. (%)</th>
   <th colspan="3">Najlepsza oferta kupna</th>
   <th colspan="3">Najlepsza oferta sprzedaży</th>
   <th rowspan="2">Wol. ost. tr.</th><th rowspan="2">Liczba transakcji</th>
   <th rowspan="2">Wol. obr. - skumul.</th>
   <th rowspan="2">Wart. obr. - skumul. (tys.)</th><th rowspan="2">MIC</th>
  </tr>
  <tr>
   <th>Liczba zleceń</th><th>Wolumen</th><th>Limit</th>
   <th>Limit</th><th>Wolumen</th><th>Liczba zleceń</th>
  </tr>
 </thead>
 <tbody>
  <tr>
   <td class="left col0"><a href="javascript:void()"><img alt=""/></a></td>
   <td class="left col1"></td>
   <td class="left col2"><a href="spolka?isin=PLKGHM000017">KGHM</a></td>
   <td class="left col3">PLKGHM000017</td>
   <td class="left col4">KGH</td>
   <td class="left col5">PLN</td>
   <td class="text-center col6">17:00:41</td>
   <td class="col7 right">304,3000</td>
   <td class="col8 right">-</td>
   <td class="col9 right">305,0000</td>
   <td class="col10 right">300,0000</td>
   <td class="col11 right">306,5000</td>
   <td class="col12 right">302,5000</td>
   <td class="left col13"></td>
   <td class="col14 right">-0,59</td>
   <td class="col15 right">2</td><td class="col15 right">244</td>
   <td class="col15 right">302,4000</td>
   <td class="col16 right">302,6000</td><td class="col16 right">1</td>
   <td class="col16 right">1</td>
   <td class="col17 right">100</td>
   <td class="col18 right">5 432</td>
   <td class="col19 right">1 234 567</td>
   <td class="col20 right">373 456,78</td>
   <td class="left col21">XWAR</td>
  </tr>
  <tr>
   <td class="left col0"></td><td class="left col1"></td>
   <td class="left col2"><a href="spolka?isin=PLNFI0600010">06MAGNA</a></td>
   <td class="left col3">PLNFI0600010</td>
   <td class="left col4">06N</td>
   <td class="left col5">PLN</td>
   <td class="text-center col6">-</td>
   <td class="col7 right">2,4700</td>
   <td class="col8 right">-</td>
   <td class="col9 right">-</td><td class="col10 right">-</td>
   <td class="col11 right">-</td><td class="col12 right">-</td>
   <td class="left col13"></td>
   <td class="col14 right">-</td>
   <td class="col15 right">-</td><td class="col15 right">-</td>
   <td class="col15 right">-</td>
   <td class="col16 right">-</td><td class="col16 right">-</td>
   <td class="col16 right">-</td>
   <td class="col17 right">-</td><td class="col18 right">-</td>
   <td class="col19 right">-</td><td class="col20 right">-</td>
   <td class="left col21">XWAR</td>
  </tr>
 </tbody>
</table>
</body></html>
"""

# ── NewConnect: one flat <thead> row, 20 data columns, Skrót one column earlier ──
_NC_HTML = """
<html><body>
<table class="table footable">
 <thead>
  <tr>
   <th></th><th>Nazwa</th><th>ISIN</th><th>Skrót</th><th>Waluta</th><th>Segment</th>
   <th>Czas ost. Trans.</th><th>Kurs odn.</th><th>TKO</th><th>Kurs otw.</th>
   <th>Kurs min.</th><th>Kurs maks.</th><th>Kurs ost. trans.</th><th>Flaga</th>
   <th>Zm.do k.odn. (%)</th><th>Wol. ost. tr.</th><th>Liczba transakcji</th>
   <th>Wol. obr. - skumul.</th><th>Wart. obr. - skumul. (tys.)</th><th>MIC</th>
  </tr>
 </thead>
 <tbody>
  <tr>
   <td class="left col0"><a href="javascript:void()"><img alt=""/></a></td>
   <td class="left col1"><a href="spolka?isin=PLONESL00011">1SOLUTION</a></td>
   <td class="col2" style="display:none">PLONESL00011</td>
   <td class="left col3">ONE</td>
   <td class="col4" style="display:none"></td>
   <td class="left col5">NC Base</td>
   <td class="text-center col6">16:19:29</td>
   <td class="col7 right">0,0910</td>
   <td class="col8 right">-</td>
   <td class="col9 right">0,0892</td>
   <td class="col10 right">0,0890</td>
   <td class="col11 right">0,0908</td>
   <td class="col12">0,0908</td>
   <td class="col13" style="display:none"></td>
   <td class="col14">-0,22</td>
   <td class="col15">5</td>
   <td class="col16">7</td>
   <td class="col17">10 405</td>
   <td class="col18">0,93</td>
   <td class="col19" style="display:none"></td>
  </tr>
 </tbody>
</table>
</body></html>
"""

_NO_TABLE_HTML = "<html><body><p>Brak danych</p></body></html>"

# Header labels renamed — the parser must refuse rather than fall back to positions.
_UNEXPECTED_HEADERS_HTML = _NC_HTML.replace("Kurs ost. trans.", "Ostatni").replace(
    "Skrót", "Symbol"
)


def _mock_resp(html: str) -> MagicMock:
    m = MagicMock()
    m.text = html
    return m


def test_gpw_layout_is_mapped_by_expanding_colspan():
    """The two colspan=3 offer blocks must not shift the columns that follow them."""
    from src.gpw_quotations import fetch_quotations

    with patch("src.gpw_quotations.get", return_value=_mock_resp(_GPW_HTML)):
        result = fetch_quotations("gpw")

    assert set(result) == {"KGH", "06N"}
    kgh = result["KGH"]
    assert kgh["company_name"] == "KGHM"
    assert kgh["isin"] == "PLKGHM000017"
    assert kgh["kurs_zamkniecia"] == pytest.approx(302.5)
    assert kgh["kurs_odn"] == pytest.approx(304.3)
    assert kgh["kurs_otwarcia"] == pytest.approx(305.0)
    assert kgh["kurs_min"] == pytest.approx(300.0)
    assert kgh["kurs_max"] == pytest.approx(306.5)
    assert kgh["zmiana_procentowa"] == pytest.approx(-0.59)
    assert kgh["liczba_transakcji"] == 5432
    # Feed publishes thousands of PLN; the column stores PLN.
    assert kgh["wartosc_obrotu"] == pytest.approx(373_456_780.0)
    # Computed from the two prices, NOT reconstructed from the rounded percentage.
    assert kgh["zmiana_kwotowa"] == pytest.approx(-1.8)


def test_gpw_row_without_trades_yields_none_not_zero():
    """A '-' cell is missing data; writing 0.0 would render as a real price."""
    from src.gpw_quotations import fetch_quotations

    with patch("src.gpw_quotations.get", return_value=_mock_resp(_GPW_HTML)):
        result = fetch_quotations("gpw")

    magna = result["06N"]
    assert magna["kurs_zamkniecia"] is None
    assert magna["kurs_otwarcia"] is None
    assert magna["liczba_transakcji"] is None
    assert magna["wartosc_obrotu"] is None
    assert magna["kurs_odn"] == pytest.approx(2.47)
    # No close means no change to compute — not a zero move.
    assert magna["zmiana_kwotowa"] is None


def test_newconnect_layout_survives_the_shifted_identity_block():
    """Skrót sits at data index 3 here and 4 on GPW; the close label is shorter too."""
    from src.gpw_quotations import fetch_quotations

    with patch("src.gpw_quotations.get", return_value=_mock_resp(_NC_HTML)):
        result = fetch_quotations("nc")

    assert set(result) == {"ONE"}
    one = result["ONE"]
    assert one["company_name"] == "1SOLUTION"
    assert one["isin"] == "PLONESL00011"
    assert one["kurs_zamkniecia"] == pytest.approx(0.0908)
    assert one["kurs_odn"] == pytest.approx(0.0910)
    assert one["kurs_min"] == pytest.approx(0.0890)
    assert one["liczba_transakcji"] == 7
    assert one["wartosc_obrotu"] == pytest.approx(930.0)
    assert one["zmiana_kwotowa"] == pytest.approx(-0.0002)


@pytest.mark.parametrize(
    "market, patch_kwargs",
    [
        ("gpw", {"side_effect": ScraperError("timeout")}),
        ("gpw", {"return_value": _mock_resp(_NO_TABLE_HTML)}),
        ("nc", {"return_value": _mock_resp(_UNEXPECTED_HEADERS_HTML)}),
    ],
    ids=["http_failure", "table_missing", "unexpected_headers"],
)
def test_fetch_quotations_returns_empty_instead_of_guessing(market, patch_kwargs):
    """Every failure path yields {} — never a partial or positionally-guessed row."""
    from src.gpw_quotations import fetch_quotations

    with patch("src.gpw_quotations.get", **patch_kwargs):
        assert fetch_quotations(market) == {}


def test_unknown_market_returns_empty_without_fetching():
    """An unknown market must not reach the network at all."""
    from src.gpw_quotations import fetch_quotations

    with patch("src.gpw_quotations.get") as mock_get:
        assert fetch_quotations("catalyst") == {}
    mock_get.assert_not_called()
