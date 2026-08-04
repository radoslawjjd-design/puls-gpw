"""Tests for the GPW archive reader (PUL-98 official-close-source).

The fixtures below are trimmed copies of the real page, captured 2026-07-28:

* the quotations sheet is **not** the first table on the page — a CALL/PUT table
  without a thead precedes it, so positional table selection would parse options
  data as prices;
* on a non-session date the quotations sheet is absent entirely while that first
  table remains, which is why "no table" means "no session" rather than "error";
* `Nazwa` cells wrap an empty toggle `span`, turnover uses `&nbsp;` thousands and
  Polish decimal commas.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.exceptions import ScraperError
from src.gpw_archive import fetch_archive_session

_HEAD = (
    "<thead><tr>"
    '<th class="left footable-sortable">Nazwa</th>'
    '<th class="text-right">Waluta</th>'
    '<th class="text-right">Kurs otwarcia</th>'
    '<th class="text-right">Kurs maksymalny</th>'
    '<th class="text-right">Kurs minimalny</th>'
    '<th class="text-right">Kurs zamknięcia</th>'
    '<th class="text-right">Zmiana kursu %</th>'
    '<th class="text-right">Wartość obrotu (w tys.)</th>'
    "</tr></thead>"
)

_OPTIONS_TABLE = (
    '<table class="table"><tbody><tr><td></td><td>CALL</td><td>PUT</td></tr></tbody></table>'
)


def _row(name, otw, maks, mini, close, pct, obrot):
    return (
        f'<tr><td class="left"><span class="footable-toggle"></span>{name}</td>'
        f'<td class="text-right">PLN</td>'
        f'<td class="text-right">{otw}</td><td class="text-right">{maks}</td>'
        f'<td class="text-right">{mini}</td><td class="text-right">{close}</td>'
        f'<td class="text-right">{pct}</td><td class="text-right">{obrot}</td></tr>'
    )


_SESSION_PAGE = (
    "<html><body>" + _OPTIONS_TABLE + '<table class="table footable">' + _HEAD + "<tbody>"
    + _row("ALLEGRO", "44,6000", "45,0850", "44,4000", "44,7350", "-0,33", "198&nbsp;842,69")
    + _row("06MAGNA", "2,5000", "2,5000", "2,4700", "2,4700", "-1,20", "0,18")
    + _row("NOTRADE", "-", "-", "-", "-", "-", "-")
    + "</tbody></table></body></html>"
)

_NON_SESSION_PAGE = "<html><body>" + _OPTIONS_TABLE + "</body></html>"

_DRIFTED_PAGE = (
    "<html><body>" + _OPTIONS_TABLE + '<table class="table footable">'
    + _HEAD.replace("Kurs zamknięcia", "Kurs rozliczeniowy")
    + "<tbody>"
    + _row("ALLEGRO", "44,6000", "45,0850", "44,4000", "44,7350", "-0,33", "198&nbsp;842,69")
    + "</tbody></table></body></html>"
)


def _response(html):
    resp = MagicMock()
    resp.text = html
    return resp


@pytest.fixture(autouse=True)
def _reset_pacing():
    """The module paces fetches at 1.5 s; leaking that clock across tests would
    make the suite sleep for real. Each test starts from an idle module."""
    import src.gpw_archive as archive

    archive._last_fetch_at = 0.0
    yield
    archive._last_fetch_at = 0.0


def test_archive_parses_a_session_by_header_not_position():
    """Values come from the sheet behind the options table, turnover scaled ×1000."""
    with patch("src.gpw_archive.get", return_value=_response(_SESSION_PAGE)):
        sheet = fetch_archive_session(date(2026, 7, 24))

    assert set(sheet) == {"ALLEGRO", "06MAGNA", "NOTRADE"}
    allegro = sheet["ALLEGRO"]
    assert allegro["kurs_zamkniecia"] == 44.735
    assert allegro["kurs_otwarcia"] == 44.6
    assert allegro["kurs_max"] == 45.085
    assert allegro["kurs_min"] == 44.4
    assert allegro["zmiana_procentowa"] == -0.33
    assert allegro["wartosc_obrotu"] == pytest.approx(198_842_690.0)


def test_archive_reports_a_dash_as_missing_not_zero():
    """A row with no trades must not become a 0.00 close — that is a real price."""
    with patch("src.gpw_archive.get", return_value=_response(_SESSION_PAGE)):
        sheet = fetch_archive_session(date(2026, 7, 24))

    assert sheet["NOTRADE"]["kurs_zamkniecia"] is None
    assert sheet["NOTRADE"]["wartosc_obrotu"] is None


def test_archive_non_session_date_returns_empty_not_an_error():
    """A weekend or holiday simply has no sheet; callers treat {} as 'no session'."""
    with patch("src.gpw_archive.get", return_value=_response(_NON_SESSION_PAGE)):
        assert fetch_archive_session(date(2026, 7, 25)) == {}


def test_archive_exhausted_retries_raise_rather_than_look_like_a_holiday():
    """Conflating a network fault with {} would silently skip a real session."""
    with patch("src.gpw_archive.get", side_effect=ScraperError("all attempts failed")):
        with patch("src.gpw_archive.time.sleep"):
            with pytest.raises(ScraperError):
                fetch_archive_session(date(2026, 7, 24))


def test_archive_retries_a_transient_failure():
    """The archive resets connections at rest; one reset must not lose the session."""
    responses = [ScraperError("reset"), _response(_SESSION_PAGE)]

    def flaky(url):
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    with patch("src.gpw_archive.get", side_effect=flaky):
        with patch("src.gpw_archive.time.sleep"):
            sheet = fetch_archive_session(date(2026, 7, 24))

    assert sheet["ALLEGRO"]["kurs_zamkniecia"] == 44.735
    assert responses == []


def test_archive_fails_loud_when_the_header_changes():
    """A renamed close column must stop the run, not yield a sheet without closes."""
    with patch("src.gpw_archive.get", return_value=_response(_DRIFTED_PAGE)):
        with pytest.raises(ScraperError, match="header"):
            fetch_archive_session(date(2026, 7, 24))


def test_archive_requests_the_date_and_instrument_type_the_site_expects():
    """DD-MM-YYYY, and the type code selects the instrument class."""
    with patch("src.gpw_archive.get", return_value=_response(_SESSION_PAGE)) as mock_get:
        with patch("src.gpw_archive.time.sleep"):
            fetch_archive_session(date(2026, 7, 24))
            url = mock_get.call_args[0][0]
            assert "date=24-07-2026" in url
            assert "type=10" in url

            fetch_archive_session(date(2026, 7, 24), instrument_type="241")
            assert "type=241" in mock_get.call_args[0][0]


def test_archive_paces_consecutive_fetches():
    """A 0.4 s cadence was measured to trigger ConnectionReset — pacing is required."""
    import src.gpw_archive as archive

    # The module NAME is replaced, not an attribute on the shared `time` module.
    # `patch("src.gpw_archive.time.monotonic", side_effect=[...])` reaches into
    # stdlib `time` itself, so every other thread alive in the session — the
    # uvicorn server the E2E fixture leaves running, above all — draws from that
    # four-value list too and exhausts it. That made this test fail only when the
    # suite ran in the wrong order, which is the worst kind of red.
    fake_time = MagicMock()
    fake_time.monotonic.side_effect = [100.0, 100.0, 100.2, 100.2]
    sleep = fake_time.sleep

    with patch("src.gpw_archive.get", return_value=_response(_SESSION_PAGE)):
        with patch("src.gpw_archive.time", fake_time):
            archive._last_fetch_at = 0.0
            fetch_archive_session(date(2026, 7, 24))
            sleep.assert_not_called()
            fetch_archive_session(date(2026, 7, 23))

    slept = sleep.call_args[0][0]
    assert slept == pytest.approx(archive._MIN_FETCH_INTERVAL_S - 0.2, abs=1e-6)
