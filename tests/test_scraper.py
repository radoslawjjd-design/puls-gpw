import datetime as real_datetime
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from zoneinfo import ZoneInfo

from db.bigquery import announcement_id_for_url
from src.scraper import scrape_new_announcements

_WARSAW = ZoneInfo("Europe/Warsaw")
_FIXTURES = Path(__file__).parent / "fixtures"
_FIXED_NOW = real_datetime.datetime(2026, 6, 5, 10, 30, 0, tzinfo=_WARSAW)

_ITEM1_URL = "https://www.bankier.pl/notowania/SFKPOLKAP/komunikaty/1"
_ITEM2_URL = "https://www.bankier.pl/notowania/TESTCOMP/komunikaty/2"

_PAGE1_HTML = (_FIXTURES / "sample_listing_page1.html").read_text(encoding="utf-8")

_ALL_IN_WINDOW_HTML = """\
<!DOCTYPE html>
<html><body>
<ul>
<li class="m-quotes-announcements-item">
  <a class="m-quotes-announcements-item__anchor" href="/notowania/ACOMP/komunikaty/10">ACOMP: Ann 10</a>
  <span class="m-quotes-announcements-item__date">05.06.2026 10:25</span>
  <div class="a-quotes-badge"><span class="value">ESPI</span></div>
</li>
<li class="m-quotes-announcements-item">
  <a class="m-quotes-announcements-item__anchor" href="/notowania/BCOMP/komunikaty/11">BCOMP: Ann 11</a>
  <span class="m-quotes-announcements-item__date">05.06.2026 10:20</span>
  <div class="a-quotes-badge"><span class="value">EBI</span></div>
</li>
</ul>
</body></html>
"""

_EMPTY_HTML = "<!DOCTYPE html><html><body></body></html>"


def _mock_resp(html: str) -> MagicMock:
    m = MagicMock()
    m.text = html
    return m


def _setup_dt(mock_dt: MagicMock) -> None:
    mock_dt.datetime.now.return_value = _FIXED_NOW
    mock_dt.datetime.strptime.side_effect = real_datetime.datetime.strptime
    mock_dt.timedelta.side_effect = real_datetime.timedelta


def test_parse_item_fields():
    with (
        patch("src.scraper.get", return_value=_mock_resp(_PAGE1_HTML)) as mock_get,
        patch("src.scraper.get_processed_ids_since", return_value=set()),
        patch("src.scraper.datetime") as mock_dt,
    ):
        _setup_dt(mock_dt)
        result = scrape_new_announcements()

    assert len(result) == 2
    ann = result[0]
    assert ann.title == "SFKPOLKAP: Tytuł ogłoszenia 1"
    assert ann.espi_code == "SFKPOLKAP"
    assert ann.bankier_url == _ITEM1_URL
    assert ann.published_at == real_datetime.datetime(2026, 6, 5, 10, 20, tzinfo=_WARSAW)
    assert ann.source == "espi"
    assert result[1].source == "ebi"


def test_dedup_filter():
    known = {announcement_id_for_url(_ITEM1_URL)}
    with (
        patch("src.scraper.get", return_value=_mock_resp(_PAGE1_HTML)),
        patch("src.scraper.get_processed_ids_since", return_value=known),
        patch("src.scraper.datetime") as mock_dt,
    ):
        _setup_dt(mock_dt)
        result = scrape_new_announcements()

    assert len(result) == 1
    assert result[0].bankier_url == _ITEM2_URL


def test_stop_condition_on_page():
    with (
        patch("src.scraper.get", return_value=_mock_resp(_PAGE1_HTML)) as mock_get,
        patch("src.scraper.get_processed_ids_since", return_value=set()),
        patch("src.scraper.datetime") as mock_dt,
    ):
        _setup_dt(mock_dt)
        scrape_new_announcements()

    mock_get.assert_called_once()


def test_pagination_continues():
    responses = [_mock_resp(_ALL_IN_WINDOW_HTML), _mock_resp(_EMPTY_HTML)]
    with (
        patch("src.scraper.get", side_effect=responses) as mock_get,
        patch("src.scraper.get_processed_ids_since", return_value=set()),
        patch("src.scraper.datetime") as mock_dt,
    ):
        _setup_dt(mock_dt)
        scrape_new_announcements()

    assert mock_get.call_count == 2


def test_max_pages_safeguard():
    with (
        patch("src.scraper.get", return_value=_mock_resp(_ALL_IN_WINDOW_HTML)) as mock_get,
        patch("src.scraper.get_processed_ids_since", return_value=set()),
        patch("src.scraper.datetime") as mock_dt,
        patch.dict(os.environ, {"MAX_PAGES_BANKIER": "2"}),
    ):
        _setup_dt(mock_dt)
        scrape_new_announcements()

    assert mock_get.call_count == 2


def test_empty_page_stops():
    with (
        patch("src.scraper.get", return_value=_mock_resp(_EMPTY_HTML)) as mock_get,
        patch("src.scraper.get_processed_ids_since", return_value=set()),
        patch("src.scraper.datetime") as mock_dt,
    ):
        _setup_dt(mock_dt)
        result = scrape_new_announcements()

    assert result == []
    mock_get.assert_called_once()
