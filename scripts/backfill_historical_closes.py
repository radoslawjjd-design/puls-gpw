"""One-time backfill of historical daily closes from stooq.pl CSV files (PUL-92).

The user downloads per-symbol CSV files manually in their browser (stooq's
"Pobierz dane w pliku csv..." on https://stooq.pl/q/d/?s=<symbol>, raw prices)
into a directory; this script matches the files to the BQ instrument universe
(companies + etf_instruments), derives zmiana_* fields, and inserts rows into
company_daily_stats / etf_quotes via insert-only MERGE — scraper-written rows
are never touched, and re-ingesting the same files is a no-op.

Live scripted fetching from stooq is impossible: the site blocks non-browser
TLS fingerprints (see plan Addendum, 2026-07-24).

Run with:
    uv run python scripts/backfill_historical_closes.py --from-dir "C:/pobrane/stooq" --dry-run
    uv run python scripts/backfill_historical_closes.py --from-dir "C:/pobrane/stooq"
    uv run python scripts/backfill_historical_closes.py --from-dir "C:/pobrane/stooq" --tickers KRU,ETFBW20TR

Requires ADC: gcloud auth application-default login
"""
import argparse
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.logging_setup import configure_logging

configure_logging()

from db.bigquery import (
    _get_client,
    _table_ref,
    create_company_daily_stats_table_if_not_exists,
    create_etf_quotes_table_if_not_exists,
    ensure_company_daily_stats_schema_current,
    ensure_etf_quotes_schema_current,
    merge_company_daily_stats_insert_only,
    merge_etf_quotes_insert_only,
)

logger = logging.getLogger(__name__)

_ROUND_PRICE = 4
_ROUND_PCT = 2


# ── Pure logic (unit-tested in tests/test_backfill_historical_closes.py) ──────


def map_symbol(ticker: str, kind: str) -> str:
    """App ticker -> stooq symbol: stocks lowercased, ETFs lowercased + '.pl'."""
    sym = ticker.lower()
    return f"{sym}.pl" if kind == "etf" else sym


def normalize_stem(filename: str) -> str:
    """Downloaded filename -> bare stooq symbol stem.

    Tolerates stooq's `<symbol>_d.csv` naming, the `.pl` ETF suffix, and
    browser duplicate-download suffixes like ` (1)`.
    """
    stem = filename.lower()
    stem = re.sub(r"\.csv$", "", stem)
    stem = re.sub(r"\s*\(\d+\)$", "", stem)
    stem = re.sub(r"_d$", "", stem)
    stem = re.sub(r"\.pl$", "", stem)
    return stem


def match_files_to_tickers(
    filenames: list[str], universe: list[tuple[str, str]]
) -> tuple[list[tuple[str, str, str]], list[str], list[str]]:
    """Match downloaded files to universe tickers by normalized symbol stem.

    Returns (matches as (filename, ticker, kind), unmatched files,
    universe tickers with no file).
    """
    by_stem = {
        normalize_stem(map_symbol(ticker, kind)): (ticker, kind)
        for ticker, kind in universe
    }
    matches: list[tuple[str, str, str]] = []
    unmatched: list[str] = []
    seen_tickers: set[str] = set()
    for name in filenames:
        hit = by_stem.get(normalize_stem(name))
        if hit is None:
            unmatched.append(name)
        else:
            matches.append((name, hit[0], hit[1]))
            seen_tickers.add(hit[0])
    missing = [t for t, _ in universe if t not in seen_tickers]
    return matches, unmatched, missing


def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_stooq_csv(text: str) -> list[dict]:
    """Parse stooq CSV (Data,Otwarcie,Najwyzszy,Najnizszy,Zamkniecie,Wolumen).

    Rows without a parseable date or close are skipped. Returns dicts with
    keys: date, open, high, low, close, volume.
    """
    rows: list[dict] = []
    for line in text.strip().splitlines()[1:]:
        parts = line.strip().split(",")
        if len(parts) < 6:
            continue
        close = _to_float(parts[4])
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[0]) or close is None:
            continue
        rows.append(
            {
                "date": parts[0],
                "open": _to_float(parts[1]),
                "high": _to_float(parts[2]),
                "low": _to_float(parts[3]),
                "close": close,
                "volume": _to_float(parts[5]),
            }
        )
    return rows


def _round_or_none(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def build_rows(ticker: str, parsed: list[dict], kind: str, fetched_at: str) -> list[dict]:
    """Build BQ rows with derived zmiana_* (and ETF kurs_odn) from prior closes.

    Sorts ascending first; the earliest row gets None derived fields (no prior
    close — matches scraper semantics for no-reference days).
    """
    out: list[dict] = []
    prev_close: float | None = None
    for p in sorted(parsed, key=lambda r: r["date"]):
        close = p["close"]
        row: dict = {
            "ticker": ticker,
            "snapshot_date": p["date"],
            "kurs_zamkniecia": round(close, _ROUND_PRICE),
            "kurs_otwarcia": _round_or_none(p["open"], _ROUND_PRICE),
            "kurs_max": _round_or_none(p["high"], _ROUND_PRICE),
            "kurs_min": _round_or_none(p["low"], _ROUND_PRICE),
            "fetched_at": fetched_at,
        }
        if prev_close is not None:
            row["zmiana_kwotowa"] = round(close - prev_close, _ROUND_PRICE)
            row["zmiana_procentowa"] = round((close / prev_close - 1) * 100, _ROUND_PCT)
        else:
            row["zmiana_kwotowa"] = None
            row["zmiana_procentowa"] = None
        if kind == "etf":
            row["kurs_odn"] = _round_or_none(prev_close, _ROUND_PRICE)
            row["wolumen_skum"] = p["volume"]
        out.append(row)
        prev_close = close
    return out


def classify_response(text: str) -> str:
    """Classify file/HTTP content: ok | challenge | limit | denied | unknown.

    Guards --from-dir against accidentally saved stooq error/limit pages.
    """
    if text.startswith("Data,"):
        return "ok"
    if "__verify" in text or "crypto.subtle" in text:
        return "challenge"
    if "Przekroczony dzienny limit" in text or "Przepisz powyższy kod" in text:
        return "limit"
    if "Odmowa dostępu" in text:
        return "denied"
    return "unknown"


def dedup_rows(rows: list[dict]) -> list[dict]:
    """Keep one row per (ticker, snapshot_date) — first occurrence wins."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for row in rows:
        key = (row["ticker"], row["snapshot_date"])
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


# ── BQ universe (script-local, read-only) ─────────────────────────────────────


def load_universe(client) -> list[tuple[str, str]]:
    """All known tickers as (ticker, kind); a ticker in both tables is 'stock'
    (mirrors consumer COALESCE precedence)."""
    companies = _table_ref(client, "companies")
    etfs = _table_ref(client, "etf_instruments")
    query = f"""
        SELECT ticker, kind FROM (
          SELECT ticker, 'stock' AS kind, 0 AS pri FROM `{companies}`
          UNION ALL
          SELECT ticker, 'etf' AS kind, 1 AS pri FROM `{etfs}`
        )
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY pri) = 1
        ORDER BY ticker
    """
    return [(r.ticker, r.kind) for r in client.query(query).result()]


# ── Main ──────────────────────────────────────────────────────────────────────


def _flush(stock_rows: list[dict], etf_rows: list[dict], dry_run: bool) -> int:
    inserted = 0
    if stock_rows:
        if dry_run:
            logger.info("[dry-run] would merge %d stock rows", len(stock_rows))
        else:
            inserted += merge_company_daily_stats_insert_only(dedup_rows(stock_rows))
        stock_rows.clear()
    if etf_rows:
        if dry_run:
            logger.info("[dry-run] would merge %d ETF rows", len(etf_rows))
        else:
            inserted += merge_etf_quotes_insert_only(dedup_rows(etf_rows))
        etf_rows.clear()
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-dir", required=True, help="directory with downloaded stooq CSV files")
    parser.add_argument("--dry-run", action="store_true", help="parse+report only, no BQ writes")
    parser.add_argument("--tickers", help="comma-separated app tickers subset, e.g. KRU,ETFBW20TR")
    parser.add_argument("--chunk-size", type=int, default=25, help="tickers per BQ flush (default 25)")
    args = parser.parse_args()

    src_dir = Path(args.from_dir)
    if not src_dir.is_dir():
        print(f"error: {src_dir} is not a directory", file=sys.stderr)
        return 1
    filenames = sorted(p.name for p in src_dir.glob("*.csv"))
    if not filenames:
        print(f"error: no *.csv files in {src_dir}", file=sys.stderr)
        return 1

    client = _get_client()
    universe = load_universe(client)
    if args.tickers:
        wanted = {t.strip().upper() for t in args.tickers.split(",")}
        universe = [(t, k) for t, k in universe if t in wanted]

    matches, unmatched, missing = match_files_to_tickers(filenames, universe)

    if not args.dry_run:
        create_company_daily_stats_table_if_not_exists()
        ensure_company_daily_stats_schema_current()
        create_etf_quotes_table_if_not_exists()
        ensure_etf_quotes_schema_current()

    fetched_at = datetime.now(timezone.utc).isoformat()
    stock_rows: list[dict] = []
    etf_rows: list[dict] = []
    stats = {"ingested": 0, "bad_content": 0, "inserted": 0}
    bad_files: list[str] = []

    try:
        for i, (name, ticker, kind) in enumerate(matches, start=1):
            text = (src_dir / name).read_text(encoding="utf-8", errors="replace")
            verdict = classify_response(text)
            if verdict != "ok":
                stats["bad_content"] += 1
                bad_files.append(f"{name} ({verdict})")
                logger.warning("%s: content is not stooq CSV (%s) — skipped", name, verdict)
                continue
            rows = build_rows(ticker, parse_stooq_csv(text), kind, fetched_at)
            logger.info("%s -> %s (%s): %d rows %s..%s", name, ticker, kind, len(rows),
                        rows[0]["snapshot_date"] if rows else "-",
                        rows[-1]["snapshot_date"] if rows else "-")
            if args.dry_run and rows:
                logger.info("[dry-run] sample row: %s", rows[-1])
            (etf_rows if kind == "etf" else stock_rows).extend(rows)
            stats["ingested"] += 1
            if i % args.chunk_size == 0:
                stats["inserted"] += _flush(stock_rows, etf_rows, args.dry_run)
    finally:
        stats["inserted"] += _flush(stock_rows, etf_rows, args.dry_run)

    print("-- backfill summary --------------------------")
    print(f"  files:      {len(filenames)} in {src_dir}")
    print(f"  ingested:   {stats['ingested']}")
    print(f"  bad content:{stats['bad_content']} {bad_files if bad_files else ''}")
    print(f"  unmatched:  {len(unmatched)} {unmatched[:10] if unmatched else ''}")
    print(f"  no file yet:{len(missing)} tickers of {len(universe)} in universe")
    print(f"  inserted:   {stats['inserted']} rows{' (dry-run: 0 written)' if args.dry_run else ''}")
    print("----------------------------------------------")
    return 0


if __name__ == "__main__":
    sys.exit(main())
