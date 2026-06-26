"""Unit tests for company_stats_main.py — entrypoint orchestration."""
from unittest.mock import MagicMock

import pytest

import company_stats_main
from db.bigquery import BigQueryError

_FAKE_LISTING = {
    "PKO": {
        "kurs_zamkniecia": 103.62,
        "zmiana_procentowa": -0.56,
        "zmiana_kwotowa": -0.58,
        "kurs_otwarcia": 104.0,
        "kurs_min": 103.4,
        "kurs_max": 104.2,
        "wartosc_obrotu": 51_810_000.0,
        "liczba_transakcji": 5_000,
    }
}

_COMPANY_PKO = {
    "ticker": "PKO",
    "name": "PKO BP",
    "hop_url": "https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=PKO",
    "isin": "PLPKOBP00016",
}

_COMPANY_CDR = {
    "ticker": "CDR",
    "name": "CD Projekt",
    "hop_url": "https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=CDR",
    "isin": "PLCDN0000017",
}


@pytest.fixture
def m(monkeypatch):
    create = MagicMock(name="create_company_daily_stats_table_if_not_exists")
    ensure = MagicMock(name="ensure_company_daily_stats_schema_current")
    list_co = MagicMock(name="list_companies_with_hop_info", return_value=[_COMPANY_PKO])
    listing = MagicMock(name="fetch_listing_page", side_effect=[_FAKE_LISTING, {}])
    sym = MagicMock(name="symbol_from_hop_url", return_value="PKO")
    delete = MagicMock(name="delete_company_daily_stats_for_date")
    batch = MagicMock(name="batch_insert_company_daily_stats")
    alert = MagicMock(name="send_alert")

    monkeypatch.setattr(company_stats_main, "create_company_daily_stats_table_if_not_exists", create)
    monkeypatch.setattr(company_stats_main, "ensure_company_daily_stats_schema_current", ensure)
    monkeypatch.setattr(company_stats_main, "list_companies_with_hop_info", list_co)
    monkeypatch.setattr(company_stats_main, "fetch_listing_page", listing)
    monkeypatch.setattr(company_stats_main, "symbol_from_hop_url", sym)
    monkeypatch.setattr(company_stats_main, "delete_company_daily_stats_for_date", delete)
    monkeypatch.setattr(company_stats_main, "batch_insert_company_daily_stats", batch)
    monkeypatch.setattr(company_stats_main, "send_alert", alert)

    return {
        "create": create, "ensure": ensure, "list_co": list_co,
        "listing": listing, "sym": sym,
        "delete": delete, "batch": batch, "alert": alert,
    }


# ── happy path ────────────────────────────────────────────────────────────��───

def test_happy_path_calls_all_collaborators_in_order(m):
    company_stats_main.main()

    m["create"].assert_called_once()
    m["ensure"].assert_called_once()
    m["list_co"].assert_called_once()
    assert m["listing"].call_count == 2
    m["delete"].assert_called_once()
    m["batch"].assert_called_once()
    rows = m["batch"].call_args[0][0]
    assert len(rows) == 1
    assert rows[0]["ticker"] == "PKO"
    assert rows[0]["kurs_zamkniecia"] == pytest.approx(103.62)
    m["alert"].assert_not_called()


def test_happy_path_row_contains_snapshot_date_and_fetched_at(m):
    company_stats_main.main()
    rows = m["batch"].call_args[0][0]
    assert "snapshot_date" in rows[0]
    assert "fetched_at" in rows[0]


# ── skip paths ────────────────────────────────────────────────────────────────

def test_missing_hop_url_skips_ticker(m):
    """Company with no hop_url is skipped; other companies still processed."""
    m["list_co"].return_value = [
        {"ticker": "NOURL", "name": "X", "hop_url": None, "isin": "PL000000001"},
        _COMPANY_PKO,
    ]
    # sym called once for PKO (NOURL is skipped before sym)

    company_stats_main.main()

    rows = m["batch"].call_args[0][0]
    assert len(rows) == 1
    assert rows[0]["ticker"] == "PKO"
    m["alert"].assert_not_called()


def test_none_symbol_skips_ticker(m):
    """Company with no parseable symbol is skipped; others still processed."""
    m["list_co"].return_value = [_COMPANY_PKO, _COMPANY_CDR]
    m["sym"].side_effect = ["PKO", None]  # PKO valid, CDR has no symbol

    company_stats_main.main()

    rows = m["batch"].call_args[0][0]
    assert len(rows) == 1
    assert rows[0]["ticker"] == "PKO"
    m["alert"].assert_not_called()


def test_ticker_not_in_listing_skips(m):
    """Company whose symbol is absent from the listing is skipped; others processed."""
    m["list_co"].return_value = [_COMPANY_PKO, _COMPANY_CDR]
    m["sym"].side_effect = ["PKO", "CDR"]
    # default fixture listing = {PKO: ...} — CDR is absent

    company_stats_main.main()

    rows = m["batch"].call_args[0][0]
    assert len(rows) == 1
    assert rows[0]["ticker"] == "PKO"
    m["alert"].assert_not_called()


def test_all_companies_skipped_triggers_alert_and_exits(m):
    """Total scrape failure (rows empty) must alert and exit 1 without deleting."""
    m["listing"].side_effect = [{}, {}]  # both markets return nothing

    with pytest.raises(SystemExit) as exc_info:
        company_stats_main.main()

    assert exc_info.value.code == 1
    m["alert"].assert_called_once()
    m["delete"].assert_not_called()


# ── batch insert failure ──────────────────────────────────────────────────────

def test_batch_insert_failure_triggers_alert_and_exit(m):
    """BigQueryError from batch_insert must trigger send_alert + sys.exit(1)."""
    m["batch"].side_effect = BigQueryError("streaming insert failed")

    with pytest.raises(SystemExit) as exc_info:
        company_stats_main.main()

    assert exc_info.value.code == 1
    m["alert"].assert_called_once()


# ── catastrophic failure ──────────────────────────────────────────────────────

def test_catastrophic_failure_sends_alert_and_exits(m):
    m["list_co"].side_effect = BigQueryError("cannot read companies")

    with pytest.raises(SystemExit) as exc_info:
        company_stats_main.main()

    assert exc_info.value.code == 1
    m["alert"].assert_called_once()
