"""Unit tests for company_stats_main.py — entrypoint orchestration.

PUL-98 reshaped this job: the official GPW and NewConnect quotation tables are the
source of truth, bankier.pl is a gap-filler that may never overwrite an official
value, and implausibly small responses abort the run instead of becoming the day's
data. Official entries join `Skrót` directly to `companies.ticker`; the bankier
`hop_url` → symbol indirection survives only on the fallback path.
"""
from unittest.mock import MagicMock

import pytest

import company_stats_main
from db.bigquery import BigQueryError

_PKO_OFFICIAL = {
    "company_name": "PKOBP",
    "isin": "PLPKOBP00016",
    "kurs_zamkniecia": 108.0,
    "kurs_odn": 106.58,
    "kurs_otwarcia": 107.0,
    "kurs_min": 106.5,
    "kurs_max": 108.4,
    "zmiana_procentowa": 1.33,
    "zmiana_kwotowa": 1.42,
    "liczba_transakcji": 13_249,
    "wartosc_obrotu": 226_010_530.0,
}

# Same ticker, the wrong (bid/ask) close — this is the value that must lose.
_PKO_BANKIER = {
    "kurs_zamkniecia": 107.9,
    "zmiana_procentowa": 1.24,
    "zmiana_kwotowa": 1.32,
    "kurs_otwarcia": 107.0,
    "kurs_min": 106.5,
    "kurs_max": 108.4,
    "wartosc_obrotu": 200_000_000.0,
    "liczba_transakcji": 12_000,
}

# A company in neither official feed — the reason bankier stays around at all.
_OLD_BANKIER = {
    "kurs_zamkniecia": 12.5,
    "zmiana_procentowa": 0.4,
    "zmiana_kwotowa": 0.05,
    "kurs_otwarcia": 12.45,
    "kurs_min": 12.4,
    "kurs_max": 12.6,
    "wartosc_obrotu": 150_000.0,
    "liczba_transakcji": 30,
}


def _pad(prefix: str, count: int) -> dict[str, dict]:
    """Filler instruments so the fixture clears the real coverage floors."""
    return {
        f"{prefix}{i}": {**_PKO_OFFICIAL, "company_name": f"{prefix}{i}", "isin": None}
        for i in range(count)
    }


def _company(ticker: str, isin: str | None = None, hop_url: str | None = "x") -> dict:
    url = (
        f"https://www.bankier.pl/inwestowanie/profile/quote.html?symbol={ticker}"
        if hop_url
        else None
    )
    return {"ticker": ticker, "name": ticker, "hop_url": url, "isin": isin}


@pytest.fixture
def m(monkeypatch):
    gpw = {"PKO": _PKO_OFFICIAL, **_pad("G", company_stats_main.MIN_GPW_ROWS)}
    nc = _pad("N", company_stats_main.MIN_NC_ROWS)

    mocks = {
        "create": MagicMock(name="create_company_daily_stats_table_if_not_exists"),
        "ensure": MagicMock(name="ensure_company_daily_stats_schema_current"),
        "list_co": MagicMock(name="list_companies_with_hop_info",
                             return_value=[_company("PKO", "PLPKOBP00016")]),
        "quotes": MagicMock(name="fetch_quotations",
                            side_effect=lambda market: gpw if market == "gpw" else nc),
        "listing": MagicMock(name="fetch_listing_page",
                             side_effect=lambda market: {"OLD": _OLD_BANKIER}),
        "sym": MagicMock(name="symbol_from_hop_url", side_effect=lambda url: url.rsplit("=", 1)[-1]),
        "merge": MagicMock(name="merge_company_daily_stats"),
        "alert": MagicMock(name="send_alert"),
    }
    names = {
        "create": "create_company_daily_stats_table_if_not_exists",
        "ensure": "ensure_company_daily_stats_schema_current",
        "list_co": "list_companies_with_hop_info",
        "quotes": "fetch_quotations",
        "listing": "fetch_listing_page",
        "sym": "symbol_from_hop_url",
        "merge": "merge_company_daily_stats",
        "alert": "send_alert",
    }
    for key, attr in names.items():
        monkeypatch.setattr(company_stats_main, attr, mocks[key])
    mocks["gpw"], mocks["nc"] = gpw, nc
    return mocks


def _rows_by_ticker(merge_mock) -> dict[str, dict]:
    return {r["ticker"]: r for r in merge_mock.call_args[0][0]}


# ── source priority ───────────────────────────────────────────────────────────

def test_official_value_wins_over_bankier_for_the_same_ticker(m):
    """The whole point of PUL-98: a bankier close may never replace an official one."""
    m["list_co"].return_value = [_company("PKO", "PLPKOBP00016"), _company("OLD")]
    m["listing"].side_effect = lambda market: {"PKO": _PKO_BANKIER, "OLD": _OLD_BANKIER}

    company_stats_main.main()

    rows = _rows_by_ticker(m["merge"])
    assert rows["PKO"]["kurs_zamkniecia"] == pytest.approx(108.0)
    assert rows["PKO"]["source"] == "gpw"
    assert rows["PKO"]["kurs_odn"] == pytest.approx(106.58)


def test_company_in_neither_feed_falls_back_to_bankier(m):
    """~47 companies are in no official feed; they keep a price but carry no kurs_odn."""
    m["list_co"].return_value = [_company("PKO", "PLPKOBP00016"), _company("OLD")]

    company_stats_main.main()

    rows = _rows_by_ticker(m["merge"])
    assert rows["OLD"]["kurs_zamkniecia"] == pytest.approx(12.5)
    assert rows["OLD"]["source"] == "bankier"
    assert rows["OLD"]["kurs_odn"] is None


def test_bankier_is_not_fetched_when_every_company_resolves_officially(m):
    """No unresolved company means no reason to touch the fallback source at all."""
    company_stats_main.main()

    m["listing"].assert_not_called()
    assert _rows_by_ticker(m["merge"])["PKO"]["source"] == "gpw"


def test_official_row_needs_no_hop_url(m):
    """Official matching joins Skrót to companies.ticker — the bankier hop is gone."""
    m["list_co"].return_value = [_company("PKO", "PLPKOBP00016", hop_url=None)]

    company_stats_main.main()

    assert _rows_by_ticker(m["merge"])["PKO"]["source"] == "gpw"


# ── identity guard ────────────────────────────────────────────────────────────

def test_isin_conflict_skips_only_the_conflicting_ticker(m):
    """Zero conflicts exist today, so any occurrence means the identity join moved.

    The skip must stay local — one suspect ticker cannot cost the rest of the run.
    """
    m["list_co"].return_value = [_company("PKO", "PL_SOMETHING_ELSE"), _company("G0")]

    company_stats_main.main()

    rows = _rows_by_ticker(m["merge"])
    assert "PKO" not in rows
    assert rows["G0"]["source"] == "gpw"
    m["alert"].assert_not_called()


# ── coverage floors ───────────────────────────────────────────────────────────

def test_below_floor_gpw_response_aborts_without_writing(m):
    """A hollow HTTP 200 must not become the day's data — `if not rows` cannot see it."""
    thin = {"PKO": _PKO_OFFICIAL, **_pad("G", company_stats_main.MIN_GPW_ROWS - 2)}
    m["quotes"].side_effect = lambda market: thin if market == "gpw" else m["nc"]

    with pytest.raises(SystemExit) as exc_info:
        company_stats_main.main()

    assert exc_info.value.code == 1
    m["alert"].assert_called_once()
    m["merge"].assert_not_called()


def test_below_floor_newconnect_response_aborts_without_writing(m):
    thin = _pad("N", company_stats_main.MIN_NC_ROWS - 2)
    m["quotes"].side_effect = lambda market: m["gpw"] if market == "gpw" else thin

    with pytest.raises(SystemExit) as exc_info:
        company_stats_main.main()

    assert exc_info.value.code == 1
    m["alert"].assert_called_once()
    m["merge"].assert_not_called()


def test_companies_absent_from_every_source_do_not_alert(m):
    """A normal partial gap is not a failure — only an implausible feed size is."""
    m["list_co"].return_value = [_company("PKO", "PLPKOBP00016"), _company("GHOST")]
    m["listing"].side_effect = lambda market: {}

    company_stats_main.main()

    rows = _rows_by_ticker(m["merge"])
    assert set(rows) == {"PKO"}
    m["alert"].assert_not_called()


# ── row shape ─────────────────────────────────────────────────────────────────

def test_row_carries_snapshot_date_and_fetched_at(m):
    company_stats_main.main()
    row = _rows_by_ticker(m["merge"])["PKO"]
    assert "snapshot_date" in row
    assert "fetched_at" in row
    assert "company_name" not in row


# ── failure handling ──────────────────────────────────────────────────────────

def test_merge_failure_triggers_alert_and_exit(m):
    m["merge"].side_effect = BigQueryError("merge failed")

    with pytest.raises(SystemExit) as exc_info:
        company_stats_main.main()

    assert exc_info.value.code == 1
    m["alert"].assert_called_once()


def test_catastrophic_failure_sends_alert_and_exits(m):
    m["list_co"].side_effect = BigQueryError("cannot read companies")

    with pytest.raises(SystemExit) as exc_info:
        company_stats_main.main()

    assert exc_info.value.code == 1
    m["alert"].assert_called_once()
