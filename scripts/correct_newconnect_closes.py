"""Corrective pass for NewConnect closes, from stooq's unadjusted series (PUL-96).

PUL-92 backfilled history from the stooq bulk archive (`d_pl_txt`), whose prices are
dividend-adjusted rather than the prices quoted on the day. PUL-98 repaired that for the
main market out of `gpw.pl/archiwum-notowan` and closed GH #191 — but the archive covers
only the main market, so every NewConnect name kept the defect. Measured on BAC: 208 of
250 rows in the visible year understated by exactly 3.42%.

The archive has no NewConnect twin, and four other avenues are measured dead (see
`context/changes/newconnect-raw-closes/research.md`). What works is stooq's own `o=`
bitmask, which disables each adjustment class:

    https://stooq.pl/q/d/l/?s=<symbol>&i=d&o=1111111

Scripted fetching is blocked by TLS fingerprinting (PUL-92 Addendum 2026-07-24), so the
file has to come through a real browser, and stooq rate-limits per-symbol downloads to a
handful a day. That is why this pass reads a directory instead of fetching, and why it is
built to repair one ticker at a time rather than assuming a bulk run.

**A download taken without `o=1111111` parses perfectly and is silently adjusted.**
`src.stooq_raw.assert_unadjusted` refuses it by comparing against `d_pl_txt`, whose
fractional volumes mark exactly which rows carry a factor. Do not weaken that check: the
obvious substitutes were measured and fail — a per-symbol download rounds scaled volume
to whole shares, and RTS 11 ticks reach 0.001 so "too many decimals" flags real quotes.

What gets written, and why only this:

* `kurs_zamkniecia` — the raw close, snapped back onto the tick (stooq leaves float
  round-trip noise from dividing the factor out).
* `zmiana_procentowa` — **carried through unchanged.** A percentage change is invariant
  under a constant factor, so the stored value is already correct; measured 248 of 249
  agreeing on BAC, the exception being the ex-dividend day itself.
* `zmiana_kwotowa` — re-derived from the raw close and that percentage via PUL-98's
  `derive_zmiana_kwotowa`, never by differencing consecutive closes. Differencing is
  wrong across exactly the corporate action this repair is about.

Reporting is the default; `--apply` is required to write, because the MERGE overwrites in
place with no undo.

Run with:
    uv run python scripts/correct_newconnect_closes.py --from-dir stooq_raw
    uv run python scripts/correct_newconnect_closes.py --from-dir stooq_raw --tickers BAC --apply
    uv run python scripts/correct_newconnect_closes.py --report-contaminated

Requires ADC: gcloud auth application-default login
"""
import argparse
import importlib.util
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

from src.stooq_raw import (  # noqa: E402
    AdjustedSeriesError,
    UnverifiableSeriesError,
    assert_unadjusted,
    normalise_close,
)

_ROOT = Path(__file__).parent.parent
_BULK_ROOT = _ROOT / "d_pl_txt" / "data" / "daily" / "pl"
# Both trees, not just NewConnect. The defect follows the PUL-92 backfill, not a market:
# MCR sits in `wse stocks` and is contaminated up to 2025-10-31, months before it enters
# the GPW archive PUL-98 corrected from — so it was never reachable either.
_BULK_DIRS = (_BULK_ROOT / "nc stocks", _BULK_ROOT / "wse stocks")

RAW_SOURCE = "stooq_raw"

# A stored close within this of the raw one needs no repair. Matches PUL-98 so the two
# passes agree on what "already correct" means.
CLOSE_EPSILON = 1e-4


def _load_sibling(name: str):
    """Import a sibling script that is not packaged, the way the tests do.

    Cached: these modules run `configure_logging()` and open a BigQuery client at import
    time, and `build_correction_rows` calls this once per invocation — re-executing per
    call would duplicate log handlers and re-pay that cost for nothing.
    """
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def build_correction_rows(
    ticker: str,
    raw_rows: list[dict],
    stored: dict[str, dict],
    fetched_at: str,
) -> list[dict]:
    """Rows whose stored close disagrees with the raw series.

    `stored` maps ISO date -> {"close", "pct"}. Four things are deliberately skipped:

    * dates the table does not hold — the trading-day spine is `SELECT DISTINCT
      snapshot_date`, so a stray date must never reach it;
    * closes that already agree, which would burn a partition modification for nothing;
    * rows with no parseable raw close;
    * rows with no stored percentage. All four correction columns are assigned
      unconditionally by the MERGE, so writing None there would blank a good
      `zmiana_procentowa` and `zmiana_kwotowa` — and the calendar sums the latter
      straight into the day's P/L.
    """
    official = _load_sibling("correct_official_closes")
    rows: list[dict] = []
    for raw in raw_rows:
        stored_row = stored.get(raw["date"])
        if stored_row is None:
            continue
        if raw.get("close") is None:
            continue
        close = normalise_close(raw["close"])
        current = stored_row.get("close")
        if current is not None and abs(close - current) <= CLOSE_EPSILON:
            continue
        pct = stored_row.get("pct")
        if pct is None:
            continue
        rows.append({
            "ticker": ticker,
            "snapshot_date": raw["date"],
            "kurs_zamkniecia": close,
            "zmiana_procentowa": pct,
            "zmiana_kwotowa": official.derive_zmiana_kwotowa(close, pct),
            "source": RAW_SOURCE,
            "fetched_at": fetched_at,
        })
    return rows


def ticker_from_filename(path: Path) -> str:
    """`bac_d.csv` -> `BAC`; stooq names its per-symbol downloads `<symbol>_d.csv`."""
    stem = path.stem
    if stem.endswith("_d"):
        stem = stem[:-2]
    return stem.upper()


def load_bulk_reference(ticker: str) -> list[dict]:
    """The known-adjusted series for `ticker`, used only to verify the download."""
    backfill = _load_sibling("backfill_historical_closes")
    for bulk_dir in _BULK_DIRS:
        path = bulk_dir / f"{ticker.lower()}.txt"
        if path.exists():
            return backfill.parse_stooq_ascii(
                path.read_text(encoding="utf-8", errors="replace")
            )
    return []


def load_candidate(path: Path) -> list[dict]:
    """Parse a per-symbol download, rejecting stooq's error/limit/challenge pages."""
    backfill = _load_sibling("backfill_historical_closes")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    verdict = backfill.classify_response(text)
    if verdict != "ok":
        raise ValueError(f"{path.name}: not a stooq data file ({verdict})")
    return backfill.parse_stooq_csv(text)


def contaminated_tickers(
    bulk_by_ticker: dict[str, list[dict]],
    stored_by_ticker: dict[str, dict[str, float]],
) -> list[str]:
    """Tickers whose *stored* history still carries the adjustment.

    Asking the bulk archive alone would be wrong: `d_pl_txt` never changes, so a ticker
    already repaired would keep being reported. The question is what BigQuery holds, and
    it is the same question the download guard answers — on the dates the archive marks
    adjusted (fractional volume, impossible for a share count), does the stored series
    agree with the archive? If it does, it is still adjusted.

    This replaces the tick-precision heuristic used during triage, which undercounts: it
    flagged 195 of BAC's 208 wrong rows, because rounding an adjusted value to 4 decimals
    sometimes lands back on a legal tick.
    """
    out: list[str] = []
    for ticker, bulk in sorted(bulk_by_ticker.items()):
        stored = stored_by_ticker.get(ticker)
        if not stored:
            continue
        rows = [{"date": d, "close": c} for d, c in stored.items()]
        try:
            assert_unadjusted(rows, bulk)
        except AdjustedSeriesError:
            out.append(ticker)
        except UnverifiableSeriesError:
            # Nothing overlaps a known-adjusted date, so there is nothing to report.
            continue
    return out


def load_bulk_tree() -> dict[str, list[dict]]:
    """Every bulk series that carries at least one adjusted row, keyed by ticker."""
    backfill = _load_sibling("backfill_historical_closes")
    out: dict[str, list[dict]] = {}
    for bulk_dir in _BULK_DIRS:
        if not bulk_dir.exists():
            continue
        for path in sorted(bulk_dir.glob("*.txt")):
            rows = backfill.parse_stooq_ascii(
                path.read_text(encoding="utf-8", errors="replace")
            )
            if any(
                r.get("volume") is not None and abs(r["volume"] - round(r["volume"])) > 1e-9
                for r in rows
            ):
                out[path.stem.upper()] = rows
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--from-dir", help="directory of stooq per-symbol downloads (o=1111111)")
    parser.add_argument("--tickers", help="comma-separated subset, e.g. BAC")
    parser.add_argument("--since", default=None, help="ISO date; ignore sessions before it")
    # Writing is opt-in: the MERGE overwrites production in place with no undo.
    parser.add_argument("--apply", action="store_true", help="write to BigQuery (default: report only)")
    parser.add_argument("--report-contaminated", action="store_true",
                        help="list tickers whose history still carries an adjustment, then exit")
    args = parser.parse_args()

    if args.report_contaminated:
        from google.cloud import bigquery as gbq

        from db.bigquery import _COMPANY_DAILY_STATS_TABLE_NAME, _get_client, _table_ref

        bulk_by_ticker = load_bulk_tree()
        client = _get_client()
        table = _table_ref(client, _COMPANY_DAILY_STATS_TABLE_NAME)
        query = f"""
            SELECT ticker, snapshot_date, kurs_zamkniecia
            FROM `{table}`
            WHERE ticker IN UNNEST(@tickers) AND kurs_zamkniecia IS NOT NULL
        """
        job_config = gbq.QueryJobConfig(query_parameters=[
            gbq.ArrayQueryParameter("tickers", "STRING", sorted(bulk_by_ticker)),
        ])
        stored_by_ticker: dict[str, dict[str, float]] = {}
        for row in client.query(query, job_config=job_config).result():
            stored_by_ticker.setdefault(row.ticker, {})[
                row.snapshot_date.isoformat()
            ] = row.kurs_zamkniecia

        names = contaminated_tickers(bulk_by_ticker, stored_by_ticker)
        # The split is what makes this actionable. PUL-98 ran with --since 2025-01-01,
        # so most of the deep-history contamination was never in scope; only the
        # visible-year names can reach a chart today.
        cut = args.since or (date.today() - timedelta(days=365)).isoformat()
        recent = {
            ticker: {d: c for d, c in stored.items() if d >= cut}
            for ticker, stored in stored_by_ticker.items()
        }
        visible = contaminated_tickers(bulk_by_ticker, recent)

        print(f"{len(names)} tickers carry adjusted history somewhere "
              f"(of {len(bulk_by_ticker)} with an adjusted bulk series).")
        print(f"{len(visible)} of them are adjusted inside the visible year (since {cut}) "
              "-- these are the ones a chart can reach today:")
        for name in visible:
            print(f"  {name}")
        deeper = [n for n in names if n not in set(visible)]
        print(f"\n{len(deeper)} more are adjusted only before {cut}, outside the window "
              "PUL-98 corrected.")
        print("\nRepair one with:")
        print("  1. open https://stooq.pl/q/d/?s=<symbol>&o=1111111 in a browser")
        print("  2. save the CSV via 'Pobierz dane w pliku csv...' into stooq_raw/")
        print("  3. uv run python scripts/correct_newconnect_closes.py "
              "--from-dir stooq_raw --tickers <TICKER> --apply")
        return 0

    if not args.from_dir:
        print("error: pass --from-dir or --report-contaminated", file=sys.stderr)
        return 2

    from db.bigquery import _get_client, merge_company_daily_stats_close_correction

    official = _load_sibling("correct_official_closes")
    wanted = {t.strip().upper() for t in args.tickers.split(",")} if args.tickers else None
    fetched_at = datetime.now(timezone.utc).isoformat()
    client = _get_client()

    total = 0
    for path in sorted(Path(args.from_dir).glob("*.csv")):
        ticker = ticker_from_filename(path)
        if wanted is not None and ticker not in wanted:
            continue

        candidate = load_candidate(path)
        bulk = load_bulk_reference(ticker)
        try:
            assert_unadjusted(candidate, bulk)
        except (AdjustedSeriesError, UnverifiableSeriesError) as exc:
            logger.error("%s: %s", ticker, exc)
            return 1

        dates = [r["date"] for r in candidate]
        if args.since:
            dates = [d for d in dates if d >= args.since]
            candidate = [r for r in candidate if r["date"] >= args.since]
        if not dates:
            logger.info("%s: nothing in range", ticker)
            continue

        stored_by_date, _ = official.load_stored_closes(
            client, date.fromisoformat(min(dates)), date.fromisoformat(max(dates)), {ticker}
        )
        stored = {
            day.isoformat(): per_ticker[ticker]
            for day, per_ticker in stored_by_date.items()
            if ticker in per_ticker
        }

        rows = build_correction_rows(ticker, candidate, stored, fetched_at)
        logger.info("%s: %d of %d stored sessions need correcting", ticker, len(rows), len(stored))
        if not rows:
            continue

        if args.apply:
            changed = merge_company_daily_stats_close_correction(rows)
            logger.info("%s: %d rows updated", ticker, changed)
            total += changed
        else:
            sample = rows[:3]
            for row in sample:
                logger.info("  would set %s %s -> %s", row["snapshot_date"],
                            stored[row["snapshot_date"]]["close"], row["kurs_zamkniecia"])
            total += len(rows)

    verb = "updated" if args.apply else "would update"
    logger.info("%s %d rows", verb, total)
    if not args.apply:
        logger.info("reporting only — pass --apply to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
