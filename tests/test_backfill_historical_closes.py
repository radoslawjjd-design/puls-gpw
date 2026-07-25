"""Unit tests for scripts/backfill_historical_closes.py pure logic (PUL-92).

No network, no BQ — covers CSV parsing, bulk-archive ASCII parsing, derived
fields, symbol mapping, file->ticker matching (--from-dir and --from-db-dir
modes), content validation, and dedup.
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

_ASCII = (
    "<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>\n"
    "KGH,D,19970710,000000,7.57536,7.57536,7.57536,7.57536,7638261,0\n"
    "KGH,D,19970711,000000,7.73673,7.80000,7.70000,7.73673,4831366,0\n"
    "KGH,D,BADDATE,000000,1,1,1,1,1,0\n"
    "KGH,D,19970714,000000,7.8,7.9,7.7,,4000,0\n"
    "KGH,D,20260724,000000,303,306.3,301.5,303.5,467601,0\n"
)


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


def test_parse_stooq_ascii_converts_dates_and_skips_malformed():
    rows = bf.parse_stooq_ascii(_ASCII)
    # YYYYMMDD -> ISO; header, bad date and empty close are all skipped
    assert [r["date"] for r in rows] == ["1997-07-10", "1997-07-11", "2026-07-24"]
    assert rows[0]["close"] == 7.57536
    assert rows[1]["high"] == 7.8
    assert rows[2]["volume"] == 467601.0


def test_parse_stooq_ascii_feeds_build_rows():
    rows = bf.build_rows("KGH", bf.parse_stooq_ascii(_ASCII), "stock", _FETCHED_AT)
    assert rows[0]["snapshot_date"] == "1997-07-10"
    assert rows[0]["zmiana_kwotowa"] is None
    assert rows[1]["zmiana_kwotowa"] == round(7.73673 - 7.57536, 4)


def test_classify_response_accepts_bulk_ascii_header():
    assert bf.classify_response(_ASCII) == "ok"


def test_normalize_stem_handles_txt_and_pl_suffix():
    assert bf.normalize_stem("kgh.txt") == "kgh"
    assert bf.normalize_stem("etfbw20tr.pl.txt") == "etfbw20tr"
    assert bf.normalize_stem("AAPL.PL.TXT") == "aapl"
    # idempotent on a bare stem, so matching can be reused for db-dir mode
    assert bf.normalize_stem("kgh") == "kgh"


def test_collect_db_dir_files_precedence_and_dedup(tmp_path):
    for sub, names in [
        ("wse stocks", ["kgh.txt", "pko.txt"]),
        ("nc stocks", ["kgh.txt", "abk.txt"]),  # kgh also on NC -> wse wins
        ("wse etfs", ["etfbw20tr.pl.txt"]),
        ("wse bonds", ["abe0227.pl.txt"]),  # not scanned
    ]:
        d = tmp_path / sub
        d.mkdir()
        for n in names:
            (d / n).write_text("x", encoding="utf-8")

    found = bf.collect_db_dir_files(tmp_path)

    assert set(found) == {"kgh", "pko", "abk", "etfbw20tr"}
    assert found["kgh"].parent.name == "wse stocks"
    assert found["abk"].parent.name == "nc stocks"


def test_collect_db_dir_files_matches_via_existing_matcher(tmp_path):
    (tmp_path / "wse stocks").mkdir()
    (tmp_path / "wse stocks" / "kru.txt").write_text("x", encoding="utf-8")
    (tmp_path / "wse etfs").mkdir()
    (tmp_path / "wse etfs" / "etfbw20tr.pl.txt").write_text("x", encoding="utf-8")

    found = bf.collect_db_dir_files(tmp_path)
    matches, unmatched, missing = bf.match_files_to_tickers(sorted(found), _UNIVERSE)

    assert sorted((t, k) for _, t, k in matches) == [("ETFBW20TR", "etf"), ("KRU", "stock")]
    assert unmatched == []
    assert missing == ["PKO"]


def test_filter_rows_since_derives_across_the_boundary():
    rows = bf.build_rows("KGH", bf.parse_stooq_ascii(_ASCII), "stock", _FETCHED_AT)
    kept = bf.filter_rows_since(rows, "1997-07-11")

    assert [r["snapshot_date"] for r in kept] == ["1997-07-11", "2026-07-24"]
    # derived against the dropped prior day rather than reset to None
    assert kept[0]["zmiana_kwotowa"] == round(7.73673 - 7.57536, 4)


def test_filter_rows_since_none_keeps_everything():
    rows = bf.build_rows("KGH", bf.parse_stooq_ascii(_ASCII), "stock", _FETCHED_AT)
    assert len(bf.filter_rows_since(rows, None)) == len(rows)


def test_group_rows_by_year_bounds_partitions_per_merge():
    rows = [
        {"snapshot_date": "2012-01-02"},
        {"snapshot_date": "2011-01-03"},
        {"snapshot_date": "2011-06-01"},
    ]
    groups = bf.group_rows_by_year(rows)

    assert [year for year, _ in groups] == ["2011", "2012"]
    assert len(groups[0][1]) == 2 and len(groups[1][1]) == 1


def test_dedup_rows_keeps_single_row_per_key():
    rows = [
        {"ticker": "KRU", "snapshot_date": "2026-01-02", "kurs_zamkniecia": 1.0},
        {"ticker": "KRU", "snapshot_date": "2026-01-02", "kurs_zamkniecia": 2.0},
        {"ticker": "KRU", "snapshot_date": "2026-01-03", "kurs_zamkniecia": 3.0},
    ]
    out = bf.dedup_rows(rows)
    assert len(out) == 2
    assert {r["snapshot_date"] for r in out} == {"2026-01-02", "2026-01-03"}
