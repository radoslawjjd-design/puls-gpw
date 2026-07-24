"""Unit tests for scripts/backfill_historical_closes.py pure logic (PUL-92).

No network, no BQ — covers CSV parsing, derived fields, symbol mapping,
file->ticker matching (--from-dir mode), content validation, and dedup.
"""
import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "scripts" / "backfill_historical_closes.py"
_spec = importlib.util.spec_from_file_location("backfill_historical_closes", _SCRIPT)
bf = importlib.util.module_from_spec(_spec)
sys.modules["backfill_historical_closes"] = bf
_spec.loader.exec_module(bf)

_FETCHED_AT = "2026-07-24T12:00:00+00:00"

_CSV = (
    "Data,Otwarcie,Najwyzszy,Najnizszy,Zamkniecie,Wolumen\r\n"
    "2026-01-02,495.79967795614,499.99967311344,492.30003146711,498.39977480839,31756\r\n"
    "2026-01-05,499.99967311344,502.2003196025,495.10037771402,498.5,28000\r\n"
    "2026-01-07,not-a-number,,,,\r\n"
    "2026-01-08,470.0,481.7,470.4,474.0,23597\r\n"
)

_CHALLENGE_HTML = (
    '<script nonce="x">(async()=>{const c="AAAAAGpjdbeACik5e_-eED7oCV6b8NWkLs3T",'
    'd=4,t="0".repeat(d),e=new TextEncoder;'
    'await fetch("/__verify",{method:"POST"})})();</script>'
)

_UNIVERSE = [("KRU", "stock"), ("PKO", "stock"), ("ETFBW20TR", "etf")]


def test_map_symbol_stock_and_etf():
    assert bf.map_symbol("KRU", "stock") == "kru"
    assert bf.map_symbol("ETFBW20TR", "etf") == "etfbw20tr.pl"


def test_parse_stooq_csv_skips_malformed_rows():
    rows = bf.parse_stooq_csv(_CSV)
    assert [r["date"] for r in rows] == ["2026-01-02", "2026-01-05", "2026-01-08"]
    assert rows[0]["close"] == 498.39977480839
    assert rows[0]["volume"] == 31756.0


def test_build_rows_company_derives_and_rounds():
    parsed = bf.parse_stooq_csv(_CSV)
    rows = bf.build_rows("KRU", parsed, "stock", _FETCHED_AT)

    first, second = rows[0], rows[1]
    # first backfilled day has no prior close -> derived fields are None
    assert first["snapshot_date"] == "2026-01-02"
    assert first["kurs_zamkniecia"] == 498.3998  # rounded to 4 decimals
    assert first["zmiana_kwotowa"] is None
    assert first["zmiana_procentowa"] is None
    # second day derives against the first
    assert second["zmiana_kwotowa"] == round(498.5 - 498.39977480839, 4)
    assert second["zmiana_procentowa"] == round((498.5 / 498.39977480839 - 1) * 100, 2)
    # stocks carry no volume columns at all
    assert "wartosc_obrotu" not in first and "liczba_transakcji" not in first
    assert "wolumen_skum" not in first and "kurs_odn" not in first
    assert first["ticker"] == "KRU" and first["fetched_at"] == _FETCHED_AT


def test_build_rows_etf_has_kurs_odn_and_volume():
    parsed = bf.parse_stooq_csv(_CSV)
    rows = bf.build_rows("ETFBW20TR", parsed, "etf", _FETCHED_AT)

    assert rows[0]["kurs_odn"] is None
    assert rows[1]["kurs_odn"] == 498.3998  # prior close, rounded
    assert rows[0]["wolumen_skum"] == 31756.0
    assert "wartosc_obrotu" not in rows[0]


def test_build_rows_sorts_ascending_before_deriving():
    parsed = list(reversed(bf.parse_stooq_csv(_CSV)))
    rows = bf.build_rows("KRU", parsed, "stock", _FETCHED_AT)
    assert [r["snapshot_date"] for r in rows] == ["2026-01-02", "2026-01-05", "2026-01-08"]
    assert rows[0]["zmiana_kwotowa"] is None
    assert rows[1]["zmiana_kwotowa"] == round(498.5 - 498.39977480839, 4)


def test_classify_response():
    assert bf.classify_response(_CSV) == "ok"
    assert bf.classify_response("Odmowa dostępu") == "denied"
    assert bf.classify_response(_CHALLENGE_HTML) == "challenge"
    assert bf.classify_response("<html>Przekroczony dzienny limit wywolan</html>") == "limit"
    assert bf.classify_response("<html>Przepisz powyższy kod</html>") == "limit"
    assert bf.classify_response("<html>hello</html>") == "unknown"


def test_normalize_stem():
    assert bf.normalize_stem("kru_d.csv") == "kru"
    assert bf.normalize_stem("ETFBW20TR.PL_d.csv") == "etfbw20tr"
    assert bf.normalize_stem("etfbw20tr_d.csv") == "etfbw20tr"
    assert bf.normalize_stem("kru_d (1).csv") == "kru"
    assert bf.normalize_stem("kru.csv") == "kru"


def test_match_files_to_tickers():
    files = ["kru_d.csv", "etfbw20tr_d.csv", "obcy_d.csv"]
    matches, unmatched, missing = bf.match_files_to_tickers(files, _UNIVERSE)
    assert ("kru_d.csv", "KRU", "stock") in matches
    assert ("etfbw20tr_d.csv", "ETFBW20TR", "etf") in matches
    assert unmatched == ["obcy_d.csv"]
    assert missing == ["PKO"]


def test_dedup_rows_keeps_single_row_per_key():
    rows = [
        {"ticker": "KRU", "snapshot_date": "2026-01-02", "kurs_zamkniecia": 1.0},
        {"ticker": "KRU", "snapshot_date": "2026-01-02", "kurs_zamkniecia": 2.0},
        {"ticker": "KRU", "snapshot_date": "2026-01-03", "kurs_zamkniecia": 3.0},
    ]
    out = bf.dedup_rows(rows)
    assert len(out) == 2
    assert {r["snapshot_date"] for r in out} == {"2026-01-02", "2026-01-03"}
