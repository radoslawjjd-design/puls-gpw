"""Unit tests for company_stats_main.py — entrypoint orchestration.

PUL-98 reshaped this job: the official GPW and NewConnect quotation tables are the
source of truth, bankier.pl is a gap-filler that may never overwrite an official
value, and implausibly small responses abort the run instead of becoming the day's
data. Official entries join `Skrót` directly to `companies.ticker`; the bankier
`hop_url` → symbol indirection survives only on the fallback path.
"""
from datetime import date
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
        # Self-heal (PUL-98 phase 6). By default the stored close already equals
        # today's reference price, so the reconciliation is a no-op.
        "prev": MagicMock(name="get_previous_session_closes",
                          return_value=(date(2026, 7, 27), {"PKO": 106.58})),
        "archive": MagicMock(name="fetch_archive_session", return_value={}),
        "correct": MagicMock(name="merge_company_daily_stats_close_correction",
                             return_value=0),
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
        "prev": "get_previous_session_closes",
        "archive": "fetch_archive_session",
        "correct": "merge_company_daily_stats_close_correction",
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


# ── self-heal of the previous session (PUL-98 phase 6) ────────────────────────

# The archive keys rows by the exchange's abbreviated name, not by ticker — the
# live feed's `company_name` is the bridge back to `PKO`.
_ARCHIVE_PKOBP = {"kurs_zamkniecia": 106.58, "zmiana_procentowa": 1.33,
                  "kurs_otwarcia": 105.0, "kurs_min": 104.8, "kurs_max": 107.0,
                  "wartosc_obrotu": 1.0}


def test_self_heal_does_not_touch_the_archive_when_the_stored_close_agrees(m):
    """The detector is free; the archive fetch is not. No mismatch, no fetch."""
    company_stats_main.main()

    m["archive"].assert_not_called()
    m["correct"].assert_not_called()


def test_self_heal_corrects_a_wrong_close_from_the_archive(m):
    """A stored bid/ask close disagrees with today's reference price and the
    archive confirms the reference — that is the defect, and it gets repaired."""
    m["prev"].return_value = (date(2026, 7, 27), {"PKO": 107.9})
    m["archive"].return_value = {"PKOBP": _ARCHIVE_PKOBP}

    company_stats_main.main()

    m["archive"].assert_called_once_with(date(2026, 7, 27))
    rows = m["correct"].call_args[0][0]
    assert len(rows) == 1
    row = rows[0]
    assert row["ticker"] == "PKO"
    assert row["snapshot_date"] == "2026-07-27"
    assert row["kurs_zamkniecia"] == pytest.approx(106.58)
    assert row["zmiana_procentowa"] == pytest.approx(1.33)
    assert row["source"] == "archive"


def test_self_heal_leaves_an_ex_dividend_divergence_alone(m):
    """kurs_odn is adjusted for corporate actions, so it legitimately differs from
    the previous close. When the archive confirms what we stored, writing the
    reference price would reintroduce the dividend defect (GH #191)."""
    m["prev"].return_value = (date(2026, 7, 27), {"PKO": 108.0})
    m["archive"].return_value = {"PKOBP": {**_ARCHIVE_PKOBP, "kurs_zamkniecia": 108.0}}

    company_stats_main.main()

    m["archive"].assert_called_once()
    m["correct"].assert_not_called()


def test_self_heal_skips_a_ticker_the_archive_does_not_name(m):
    """~10% of names do not map. A missing archive row is a gap to log, never a
    reason to fall back to the reference price."""
    m["prev"].return_value = (date(2026, 7, 27), {"PKO": 107.9})
    m["archive"].return_value = {"SOMETHINGELSE": _ARCHIVE_PKOBP}

    company_stats_main.main()

    m["correct"].assert_not_called()


def test_self_heal_refuses_a_name_two_tickers_claim(m):
    """`official` merges GPW and NewConnect, so last-one-wins on a shared company
    name would hand a GPW-main archive close to a NewConnect ticker — a plausible
    price against the wrong company, which is the failure this change removes. The
    corrective script drops contested names; the self-heal must do the same."""
    m["nc"]["NCX"] = {**_PKO_OFFICIAL, "company_name": "PKOBP", "isin": None}
    m["list_co"].return_value = [_company("PKO", "PLPKOBP00016"), _company("NCX")]
    # Both disagree with the reference price, so both are candidates for repair.
    m["prev"].return_value = (date(2026, 7, 27), {"PKO": 107.9, "NCX": 50.0})
    m["archive"].return_value = {"PKOBP": _ARCHIVE_PKOBP}

    company_stats_main.main()

    # Without the gate, PKOBP resolves to whichever ticker was iterated last and the
    # GPW close is written against it.
    m["correct"].assert_not_called()


def test_self_heal_failure_does_not_abort_the_ingest(m):
    """Today's prices matter more than yesterday's repair."""
    m["prev"].return_value = (date(2026, 7, 27), {"PKO": 107.9})
    m["archive"].side_effect = RuntimeError("archive unreachable")

    company_stats_main.main()

    m["merge"].assert_called_once()
    m["alert"].assert_not_called()
