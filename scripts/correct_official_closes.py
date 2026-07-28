"""One-time corrective pass: rewrite stored closes from the GPW archive (PUL-98).

`company_daily_stats.kurs_zamkniecia` was written from the bankier.pl listing page,
which publishes a best-bid / best-ask / last-continuous-trade figure rather than the
official close. This walks the trailing sessions, reads each one from
`gpw.pl/archiwum-notowan`, and corrects the close and its two derived columns
through the update-only MERGE — never inserting a date the table does not hold.

Correcting from the archive also removes the dividend-adjustment defect (GH #191)
across the same window: the archive publishes the prices actually quoted on the
day, unlike the stooq history PUL-92 backfilled.

Two rules the run must never break:

* **Only exact name hits are written.** The archive carries no ticker or ISIN, only
  `Nazwa`. The map is built from the live GPW feed and intersected with `companies`;
  an ambiguous or unknown name is counted and skipped. A mis-mapped name writes a
  plausible price against the wrong company — the exact failure class this change
  exists to remove.
* **`zmiana_procentowa` comes from the archive column**, never from differencing
  consecutive closes. GPW measures it against the reference price, which is adjusted
  for corporate actions; `ECHO` on 2026-06-24 moved +0.10% officially against -7.40%
  naively. The calendar renders this quantity directly.

Run with:
    uv run python scripts/correct_official_closes.py --dry-run
    uv run python scripts/correct_official_closes.py --since 2025-01-01
    uv run python scripts/correct_official_closes.py --tickers PKO,ALE --dry-run

Requires ADC: gcloud auth application-default login
"""
import argparse
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

from db.bigquery import (  # noqa: E402
    _COMPANY_DAILY_STATS_TABLE_NAME,
    _get_client,
    _table_ref,
    merge_company_daily_stats_close_correction,
)
from src.exceptions import ScraperError  # noqa: E402
from src.gpw_archive import fetch_archive_html, parse_archive_sheet  # noqa: E402
from src.gpw_quotations import fetch_quotations  # noqa: E402

DEFAULT_SINCE = "2025-01-01"
ARCHIVE_SOURCE = "archive"

# Closes carry four decimals; below this is float noise, not a disagreement.
CLOSE_EPSILON = 1e-4

# The archive rounds the percentage to two decimals, so anything at or below this
# is presentation, not a different number.
PCT_EPSILON = 0.01

# A wall of consecutive failures means the site stopped answering. Grinding through
# the remaining dates emitting retry noise helps nobody — stop and report.
MAX_CONSECUTIVE_FAILURES = 5


# ── pure logic (unit-tested; no network, no BigQuery) ─────────────────────────

def session_dates(since: date, until: date) -> list[date]:
    """Candidate session dates — weekdays only.

    Holidays still have to be probed (no holiday calendar exists in this project),
    but weekends are ~40% of the calendar and never trade.
    """
    out: list[date] = []
    day = since
    while day <= until:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def build_name_map(
    feed: dict[str, dict], known_tickers: set[str]
) -> tuple[dict[str, str], list[str]]:
    """Map archive `Nazwa` -> ticker, keeping only unambiguous, known hits.

    Returns (map, rejected names). A name two tickers claim is dropped rather than
    resolved: guessing writes a real price against the wrong company.
    """
    claims: dict[str, set[str]] = {}
    for ticker, stats in feed.items():
        name = stats.get("company_name")
        if name:
            claims.setdefault(name, set()).add(ticker)

    name_map: dict[str, str] = {}
    rejected: list[str] = []
    for name, tickers in claims.items():
        known = tickers & known_tickers
        if len(known) == 1:
            name_map[name] = next(iter(known))
        else:
            rejected.append(name)
    return name_map, sorted(rejected)


def derive_zmiana_kwotowa(close: float | None, pct: float | None) -> float | None:
    """The change in PLN implied by the archive's own close and percentage.

    Deliberately not `close - previous_close`: the percentage is measured against
    the reference price, so close-to-close differencing is wrong across every
    ex-dividend and split.
    """
    if close is None or pct is None or pct == -100:
        return None
    return close - close / (1 + pct / 100)


def build_correction_rows(
    session_date: date,
    sheet: dict[str, dict],
    name_map: dict[str, str],
    stored: dict[str, dict],
    fetched_at: str,
) -> list[dict]:
    """Rows whose stored close *or* percentage disagrees with the archive.

    `stored` maps ticker -> {"close", "pct"}. Skips names that did not map, tickers
    with no row on this date, sessions the archive left blank (no trades — writing
    None would blank a real price), and rows where both values already agree.

    The percentage is checked as well as the close because they can be wrong
    independently: measured across 5 sampled sessions, 8 of 1 156 rows whose close
    already matched carried a percentage that did not. The calendar renders that
    number directly, so a right price with a wrong move is still a visible defect.
    """
    rows: list[dict] = []
    for name, archived in sheet.items():
        ticker = name_map.get(name)
        if ticker is None or ticker not in stored:
            continue
        close = archived.get("kurs_zamkniecia")
        if close is None:
            continue
        pct = archived.get("zmiana_procentowa")
        current = stored[ticker]
        close_agrees = (
            current.get("close") is not None
            and abs(close - current["close"]) <= CLOSE_EPSILON
        )
        pct_agrees = pct is None or (
            current.get("pct") is not None and abs(pct - current["pct"]) <= PCT_EPSILON
        )
        if close_agrees and pct_agrees:
            continue
        rows.append({
            "ticker": ticker,
            "snapshot_date": session_date.isoformat(),
            "kurs_zamkniecia": close,
            "zmiana_procentowa": pct,
            "zmiana_kwotowa": derive_zmiana_kwotowa(close, pct),
            "source": ARCHIVE_SOURCE,
            "fetched_at": fetched_at,
        })
    return rows


def find_phantom_dates(
    traded: set[date], probed: set[date], stored_counts: dict[date, int]
) -> list[tuple[date, int]]:
    """Dates the table holds rows for although the market never traded.

    They inflate the trading-day spine and double-count a session's P/L in the
    calendar. Reported only — deletion is a separate decision.

    A date counts as a phantom on two different kinds of evidence. A **weekend**
    needs none: GPW does not trade on Saturdays, and the run never probes them —
    judging weekends by probing would have hidden the one phantom production
    actually has (2026-06-27, a Saturday, 739 rows). A **weekday** must have been
    probed and come back without a session; an unprobed weekday says nothing, and
    reporting it would turn a network gap into a data-integrity claim.
    """
    phantoms = []
    for day, count in stored_counts.items():
        if not count or day in traded:
            continue
        if day.weekday() >= 5 or day in probed:
            phantoms.append((day, count))
    return sorted(phantoms)


def group_rows_by_year(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """One MERGE per year: the table is day-partitioned and BigQuery caps a job at
    4 000 modified partitions; a year is ~250."""
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        buckets.setdefault(row["snapshot_date"][:4], []).append(row)
    return sorted(buckets.items())


def cache_path(cache_dir: Path, session_date: date) -> Path:
    """One raw page per date. The cache is also the resume mechanism."""
    return Path(cache_dir) / f"archive-{session_date.isoformat()}.html"


# ── I/O ───────────────────────────────────────────────────────────────────────

def load_session_html(cache_dir: Path | None, session_date: date) -> str:
    """Cached page if we already have it, otherwise fetch and cache."""
    if cache_dir is not None:
        cached = cache_path(cache_dir, session_date)
        if cached.exists():
            return cached.read_text(encoding="utf-8")

    html = fetch_archive_html(session_date)
    if cache_dir is not None:
        cache_path(cache_dir, session_date).write_text(html, encoding="utf-8")
    return html


def load_stored_closes(
    client, since: date, until: date, tickers: set[str] | None
) -> tuple[dict[date, dict[str, float | None]], dict[date, int]]:
    """Stored close + percentage and row counts per date across the window."""
    from google.cloud import bigquery as gbq

    table = _table_ref(client, _COMPANY_DAILY_STATS_TABLE_NAME)
    query = f"""
        SELECT snapshot_date, ticker, kurs_zamkniecia, zmiana_procentowa
        FROM `{table}`
        WHERE snapshot_date BETWEEN @since AND @until
    """
    job_config = gbq.QueryJobConfig(query_parameters=[
        gbq.ScalarQueryParameter("since", "DATE", since),
        gbq.ScalarQueryParameter("until", "DATE", until),
    ])
    stored: dict[date, dict[str, dict]] = {}
    counts: dict[date, int] = {}
    for row in client.query(query, job_config=job_config).result():
        counts[row.snapshot_date] = counts.get(row.snapshot_date, 0) + 1
        if tickers is None or row.ticker in tickers:
            stored.setdefault(row.snapshot_date, {})[row.ticker] = {
                "close": row.kurs_zamkniecia, "pct": row.zmiana_procentowa,
            }
    return stored, counts


def load_known_tickers(client) -> set[str]:
    table = _table_ref(client, "companies")
    return {r.ticker for r in client.query(f"SELECT ticker FROM `{table}`").result()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default=DEFAULT_SINCE, help=f"ISO date (default {DEFAULT_SINCE})")
    parser.add_argument("--until", default=None, help="ISO date (default: yesterday)")
    parser.add_argument("--cache-dir", default=None, help="directory for cached archive pages")
    parser.add_argument("--dry-run", action="store_true", help="report only, no BQ writes")
    parser.add_argument("--tickers", help="comma-separated subset, e.g. PKO,ALE")
    parser.add_argument("--chunk-size", type=int, default=0,
                        help="max rows per MERGE (0 = one MERGE per year)")
    args = parser.parse_args()

    try:
        since = date.fromisoformat(args.since)
        until = (date.fromisoformat(args.until) if args.until
                 else datetime.now().date() - timedelta(days=1))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    tickers = {t.strip().upper() for t in args.tickers.split(",")} if args.tickers else None
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)

    client = _get_client()
    known = load_known_tickers(client)
    name_map, rejected = build_name_map(fetch_quotations("gpw"), known)
    logger.info("name map: %d exact hits, %d names rejected", len(name_map), len(rejected))

    stored, counts = load_stored_closes(client, since, until, tickers)
    dates = session_dates(since, until)
    logger.info("probing %d weekday dates from %s to %s", len(dates), since, until)

    fetched_at = datetime.now(timezone.utc).isoformat()
    corrections: list[dict] = []
    traded: set[date] = set()
    probed_dates: set[date] = set()
    no_session: set[date] = set()
    unmatched_names: set[str] = set()
    consecutive_failures = 0
    probed = 0

    for session_date in dates:
        try:
            html = load_session_html(cache_dir, session_date)
        except ScraperError:
            consecutive_failures += 1
            logger.warning("no answer for %s (%d in a row)", session_date, consecutive_failures)
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.error(
                    "gpw.pl stopped answering after %d dates — stopping. Re-run to resume "
                    "from the cache.", probed,
                )
                return 1
            continue
        consecutive_failures = 0
        probed += 1
        probed_dates.add(session_date)

        sheet = parse_archive_sheet(html, session_date)
        if not sheet:
            no_session.add(session_date)
            continue
        traded.add(session_date)
        unmatched_names.update(name for name in sheet if name not in name_map)
        corrections.extend(build_correction_rows(
            session_date, sheet, name_map, stored.get(session_date, {}), fetched_at,
        ))

    phantoms = find_phantom_dates(traded, probed_dates, counts)

    print("\n-- correction report ------------------------------------")
    print(f"  dates probed        : {probed} of {len(dates)}")
    print(f"  sessions found      : {probed - len(no_session)}")
    print(f"  non-session dates   : {len(no_session)}")
    print(f"  mapped tickers      : {len(name_map)}")
    print(f"  rejected names      : {len(rejected)}")
    print(f"  unmatched in archive: {len(unmatched_names)}")
    print(f"  corrections         : {len(corrections)}")
    if phantoms:
        print("  PHANTOM trading days (rows stored, market closed):")
        for phantom_date, count in phantoms:
            print(f"    {phantom_date}  {count} rows")
    if unmatched_names:
        sample = sorted(unmatched_names)[:20]
        print(f"  unmatched sample    : {', '.join(sample)}")

    if args.dry_run:
        print("\n  dry run — nothing written")
        return 0
    if not corrections:
        print("\n  nothing to correct")
        return 0

    total = 0
    for year, rows in group_rows_by_year(corrections):
        batches = (
            [rows] if args.chunk_size <= 0
            else [rows[i:i + args.chunk_size] for i in range(0, len(rows), args.chunk_size)]
        )
        for batch in batches:
            affected = merge_company_daily_stats_close_correction(batch)
            total += affected
            logger.info("%s: %d of %d rows corrected", year, affected, len(batch))
    print(f"\n  corrected {total} of {len(corrections)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
