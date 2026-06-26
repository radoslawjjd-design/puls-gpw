"""Unit tests for company_stats_main.py — entrypoint orchestration.

All BigQuery/HTTP side effects are mocked; no network, no real creds.
"""
from unittest.mock import MagicMock

import pytest

import company_stats_main
from db.bigquery import BigQueryError

_FAKE_STATS = {
    "kurs_odniesienia": 50.0,
    "kurs_otwarcia": 50.5,
    "kurs_min": 49.0,
    "kurs_max": 51.0,
    "wolumen_obrotu": 1_000_000,
    "wartosc_obrotu": 50_000_000.0,
    "liczba_transakcji": 5_000,
    "stopa_zwrotu_1r": 0.15,
    "kapitalizacja": 60_000_000_000.0,
    "rynek": "GPW",
    "system": "WARSET",
}

_COMPANY_PKO = {
    "ticker": "PKO",
    "name": "PKO BP",
    "hop_url": "https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=PKO",
    "isin": "PLPKOBP00016",
}


@pytest.fixture
def m(monkeypatch):
    """Patch all I/O collaborators on company_stats_main; return the mocks."""
    create = MagicMock(name="create_company_daily_stats_table_if_not_exists")
    ensure = MagicMock(name="ensure_company_daily_stats_schema_current")
    list_co = MagicMock(name="list_companies_with_hop_info", return_value=[_COMPANY_PKO])
    sym = MagicMock(name="symbol_from_hop_url", return_value="PKO")
    fetch = MagicMock(name="fetch_daily_stats", return_value=_FAKE_STATS)
    insert = MagicMock(name="insert_company_daily_stats")
    alert = MagicMock(name="send_alert")

    monkeypatch.setattr(company_stats_main, "create_company_daily_stats_table_if_not_exists", create)
    monkeypatch.setattr(company_stats_main, "ensure_company_daily_stats_schema_current", ensure)
    monkeypatch.setattr(company_stats_main, "list_companies_with_hop_info", list_co)
    monkeypatch.setattr(company_stats_main, "symbol_from_hop_url", sym)
    monkeypatch.setattr(company_stats_main, "fetch_daily_stats", fetch)
    monkeypatch.setattr(company_stats_main, "insert_company_daily_stats", insert)
    monkeypatch.setattr(company_stats_main, "send_alert", alert)

    return {
        "create": create, "ensure": ensure, "list_co": list_co,
        "sym": sym, "fetch": fetch, "insert": insert, "alert": alert,
    }


# ── happy path ────────────────────────────────────────────────────────────────

def test_happy_path_calls_all_collaborators_in_order(m):
    company_stats_main.main()

    m["create"].assert_called_once()
    m["ensure"].assert_called_once()
    m["list_co"].assert_called_once()
    m["sym"].assert_called_once_with(_COMPANY_PKO["hop_url"])
    m["fetch"].assert_called_once_with(_COMPANY_PKO["isin"], "PKO")
    m["insert"].assert_called_once()
    kw = m["insert"].call_args.kwargs
    assert kw["ticker"] == "PKO"
    assert kw["kurs_odniesienia"] == 50.0
    assert kw["wolumen_obrotu"] == 1_000_000
    m["alert"].assert_not_called()


# ── skip paths ────────────────────────────────────────────────────────────────

def test_missing_hop_url_skips_ticker(m):
    m["list_co"].return_value = [
        {"ticker": "NOURL", "name": "No URL Co", "hop_url": None, "isin": "PL000000001"}
    ]

    company_stats_main.main()

    m["sym"].assert_not_called()
    m["fetch"].assert_not_called()
    m["insert"].assert_not_called()
    m["alert"].assert_not_called()


def test_none_symbol_skips_ticker(m):
    m["sym"].return_value = None

    company_stats_main.main()

    m["fetch"].assert_not_called()
    m["insert"].assert_not_called()
    m["alert"].assert_not_called()


def test_fetch_failure_skips_ticker(m):
    m["fetch"].return_value = None

    company_stats_main.main()

    m["insert"].assert_not_called()
    m["alert"].assert_not_called()


# ── per-ticker BigQueryError on insert ───────────────────────────────────────

def test_bq_insert_failure_skips_and_continues(m):
    """A BigQueryError on insert for one ticker must not stop the loop for others."""
    cdp = {
        "ticker": "CDR",
        "name": "CD Projekt",
        "hop_url": "https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=CDR",
        "isin": "PLCDN0000017",
    }
    m["list_co"].return_value = [_COMPANY_PKO, cdp]
    m["sym"].side_effect = ["PKO", "CDR"]
    m["fetch"].return_value = _FAKE_STATS
    m["insert"].side_effect = [BigQueryError("bq fail"), None]

    company_stats_main.main()

    assert m["insert"].call_count == 2
    m["alert"].assert_not_called()


# ── catastrophic failure ──────────────────────────────────────────────────────

def test_catastrophic_failure_sends_alert_and_exits(m):
    m["list_co"].side_effect = BigQueryError("cannot read companies table")

    with pytest.raises(SystemExit) as exc_info:
        company_stats_main.main()

    assert exc_info.value.code == 1
    m["alert"].assert_called_once()
