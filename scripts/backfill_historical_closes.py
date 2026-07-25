"""One-time backfill of historical daily closes from stooq.pl CSV files (PUL-92).

The user downloads per-symbol CSV files manually in their browser (stooq's
"Pobierz dane w pliku csv..." on https://stooq.pl/q/d/?s=<symbol>, raw prices)
into a directory; this script matches the files to the BQ instrument universe
(companies + etf_instruments), derives zmiana_* fields, and inserts rows into
company_daily_stats / etf_quotes via insert-only MERGE — scraper-written rows
are never touched, and re-ingesting the same files is a no-op.

Live scripted fetching from stooq is impossible: the site blocks non-browser
TLS fingerprints (see plan Addendum, 2026-07-24).

Two input modes:
  --from-dir     per-symbol CSV downloads (Data,Otwarcie,... / YYYY-MM-DD), raw prices
  --from-db-dir  bulk archive tree d_pl_txt/data/daily/pl (<TICKER>,<PER>,<DATE>,... /
                 YYYYMMDD). NOTE: bulk-archive history is dividend-ADJUSTED, not the
                 prices actually quoted on the day — see plan Addendum 2026-07-25.

Run with:
    uv run python scripts/backfill_historical_closes.py --from-dir "C:/pobrane/stooq" --dry-run
    uv run python scripts/backfill_historical_closes.py --from-db-dir "C:/puls-gpw/d_pl_txt/data/daily/pl" --dry-run
    uv run python scripts/backfill_historical_closes.py --from-db-dir "C:/puls-gpw/d_pl_txt/data/daily/pl" --tickers KRU,ETFBW20TR

Requires ADC: gcloud auth application-default login
"""
import argparse
import logging
import re
import sys
from datetime import date, datetime, timezone
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

# Bulk-archive subdirectories that can hold universe tickers, in precedence order.
_DB_SUBDIRS = ("wse stocks", "nc stocks", "wse etfs", "wse stocks intl")

# company_daily_stats is DAY-partitioned and BigQuery caps a table at 10k partitions.
# The archive spans 10053 trading days (from 1987 via UCG's Milan history), which
# would leave under 3 years of headroom for the scraper. 2011 keeps 3916 partitions
# and ~24 years of room; override with --since.
_DEFAULT_SINCE = "2011-01-01"


# ── Pure logic (unit-tested in tests/test_backfill_historical_closes.py) ──────


def map_symbol(ticker: str, kind: str) -> str:
    """App ticker -> stooq symbol: stocks lowercased, ETFs lowercased + '.pl'."""
    sym = ticker.lower()
    return f"{sym}.pl" if kind == "etf" else sym


def normalize_stem(filename: str) -> str:
    """Downloaded filename -> bare stooq symbol stem.

    Tolerates stooq's `<symbol>_d.csv` naming, bulk-archive `<symbol>.txt`,
    the `.pl` ETF suffix, and browser duplicate-download suffixes like ` (1)`.
    Idempotent on a bare stem, so db-dir mode reuses match_files_to_tickers().
    """
    stem = filename.lower()
    stem = re.sub(r"\.(csv|txt)$", "", stem)
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


def parse_stooq_ascii(text: str) -> list[dict]:
    """Parse stooq bulk-archive ASCII rows.

    Layout: <TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>
    with <DATE> as YYYYMMDD. Header, malformed dates and rows without a
    parseable close are skipped. Returns the same dict shape as
    parse_stooq_csv() so build_rows() consumes either source unchanged.
    """
    rows: list[dict] = []
    for line in text.strip().splitlines():
        parts = line.strip().split(",")
        if len(parts) < 9:
            continue
        raw_date = parts[2]
        close = _to_float(parts[7])
        if not re.fullmatch(r"\d{8}", raw_date) or close is None:
            continue
        rows.append(
            {
                "date": f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}",
                "open": _to_float(parts[4]),
                "high": _to_float(parts[5]),
                "low": _to_float(parts[6]),
                "close": close,
                "volume": _to_float(parts[8]),
            }
        )
    return rows


def collect_db_dir_files(root: Path) -> dict[str, Path]:
    """Bulk-archive root (…/data/daily/pl) -> {normalized stem: file path}.

    Only instrument directories that can carry universe tickers are scanned;
    bonds/futures/options/funds/indicators are ignored. On a stem collision
    the earlier directory wins (a ticker on both markets is a WSE stock).
    """
    found: dict[str, Path] = {}
    for sub in _DB_SUBDIRS:
        directory = root / sub
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.txt")):
            found.setdefault(normalize_stem(path.name), path)
    return found


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
    if text.startswith("Data,") or text.startswith("<TICKER>,"):
        return "ok"
    if "__verify" in text or "crypto.subtle" in text:
        return "challenge"
    if "Przekroczony dzienny limit" in text or "Przepisz powyższy kod" in text:
        return "limit"
    if "Odmowa dostępu" in text:
        return "denied"
    return "unknown"


def parse_since(value: str | None) -> str | None:
    """Validate the --since cut-off; empty/None means "no cut".

    filter_rows_since() compares ISO date strings lexicographically, so a
    non-canonical value fails silently rather than loudly: 'garbage' sorts
    above every date and drops the whole ingest, while '10.05.2011' sorts
    below and disables the cut — and with it the guard that keeps the table
    under BigQuery's 10k-partition ceiling. Round-tripping through
    date.fromisoformat pins the value to exactly YYYY-MM-DD.
    """
    if not value:
        return None
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError(f"--since must be YYYY-MM-DD, got {value!r}")
    return value


def filter_rows_since(rows: list[dict], since: str | None) -> list[dict]:
    """Drop rows older than `since` (ISO date), keeping derived fields intact.

    Applied after build_rows() on purpose: the oldest kept row then still
    derives zmiana_* against the day before it, which the cut removes.
    """
    if not since:
        return rows
    return [r for r in rows if r["snapshot_date"] >= since]


def group_rows_by_year(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """Group rows by snapshot year, ascending.

    company_daily_stats/etf_quotes are DAY-partitioned on snapshot_date and
    BigQuery caps a single job at 4000 modified partitions; one year is ~250,
    so merging year by year keeps every job far below the ceiling.
    """
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        buckets.setdefault(row["snapshot_date"][:4], []).append(row)
    return sorted(buckets.items())


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
    """Merge buffered rows, one job per year — see group_rows_by_year()."""
    inserted = 0
    for rows, merge_fn, label in (
        (stock_rows, merge_company_daily_stats_insert_only, "stock"),
        (etf_rows, merge_etf_quotes_insert_only, "ETF"),
    ):
        if not rows:
            continue
        if dry_run:
            logger.info("[dry-run] would merge %d %s rows", len(rows), label)
        else:
            for year, group in group_rows_by_year(dedup_rows(rows)):
                logger.info("merging %s rows for %s: %d", label, year, len(group))
                inserted += merge_fn(group)
        rows.clear()
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-dir", help="directory with per-symbol stooq CSV downloads")
    parser.add_argument(
        "--from-db-dir",
        help="stooq bulk archive root, e.g. d_pl_txt/data/daily/pl (ASCII .txt tree)",
    )
    parser.add_argument("--dry-run", action="store_true", help="parse+report only, no BQ writes")
    parser.add_argument("--tickers", help="comma-separated app tickers subset, e.g. KRU,ETFBW20TR")
    parser.add_argument("--chunk-size", type=int, default=25, help="tickers per BQ flush (default 25)")
    parser.add_argument(
        "--since",
        default=_DEFAULT_SINCE,
        help=f"drop rows before this ISO date (default {_DEFAULT_SINCE}); "
             "'' ingests everything. Guards the 10k-partitions-per-table ceiling",
    )
    args = parser.parse_args()

    if bool(args.from_dir) == bool(args.from_db_dir):
        print("error: pass exactly one of --from-dir / --from-db-dir", file=sys.stderr)
        return 1

    try:
        since = parse_since(args.since)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    src_dir = Path(args.from_db_dir or args.from_dir)
    if not src_dir.is_dir():
        print(f"error: {src_dir} is not a directory", file=sys.stderr)
        return 1

    if args.from_db_dir:
        parse_file = parse_stooq_ascii
        paths_by_key = collect_db_dir_files(src_dir)
        if not paths_by_key:
            print(f"error: no *.txt files under {src_dir}/{{{', '.join(_DB_SUBDIRS)}}}",
                  file=sys.stderr)
            return 1
    else:
        parse_file = parse_stooq_csv
        paths_by_key = {p.name: p for p in sorted(src_dir.glob("*.csv"))}
        if not paths_by_key:
            print(f"error: no *.csv files in {src_dir}", file=sys.stderr)
            return 1
    filenames = sorted(paths_by_key)

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
    stats = {"ingested": 0, "bad_content": 0, "inserted": 0, "rows": 0}
    bad_files: list[str] = []

    try:
        for i, (name, ticker, kind) in enumerate(matches, start=1):
            text = paths_by_key[name].read_text(encoding="utf-8", errors="replace")
            verdict = classify_response(text)
            if verdict != "ok":
                stats["bad_content"] += 1
                bad_files.append(f"{name} ({verdict})")
                logger.warning("%s: content is not stooq data (%s) — skipped", name, verdict)
                continue
            rows = filter_rows_since(
                build_rows(ticker, parse_file(text), kind, fetched_at), since
            )
            logger.info("%s -> %s (%s): %d rows %s..%s", name, ticker, kind, len(rows),
                        rows[0]["snapshot_date"] if rows else "-",
                        rows[-1]["snapshot_date"] if rows else "-")
            if args.dry_run and rows:
                logger.info("[dry-run] sample row: %s", rows[-1])
            (etf_rows if kind == "etf" else stock_rows).extend(rows)
            stats["ingested"] += 1
            stats["rows"] += len(rows)
            if i % args.chunk_size == 0:
                stats["inserted"] += _flush(stock_rows, etf_rows, args.dry_run)
    finally:
        stats["inserted"] += _flush(stock_rows, etf_rows, args.dry_run)

    print("-- backfill summary --------------------------")
    print(f"  files:      {len(filenames)} in {src_dir}")
    print(f"  ingested:   {stats['ingested']} tickers, {stats['rows']} rows parsed")
    print(f"  bad content:{stats['bad_content']} {bad_files if bad_files else ''}")
    print(f"  unmatched:  {len(unmatched)} files with no universe ticker {unmatched[:10]}")
    print(f"  no file:    {len(missing)} tickers of {len(universe)} in universe")
    if missing and args.dry_run:
        print(f"              {', '.join(missing)}")
    print(f"  inserted:   {stats['inserted']} rows{' (dry-run: 0 written)' if args.dry_run else ''}")
    print("----------------------------------------------")
    return 0


if __name__ == "__main__":
    sys.exit(main())
