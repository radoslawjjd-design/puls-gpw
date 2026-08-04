"""BigQuery client, schema definition, and CRUD wrappers for the announcements table.

Nullable field semantics (NULL = step not yet reached or failed):
  company, ticker        — set by parser (update_parsed_content); NULL if parse failed
  parsed_content         — set by parser; NULL if parse failed; analyzer skips if NULL
  analyzed_at            — set by save_analysis_result; NULL if analyzer skipped/failed
  structured_analysis    — set by save_analysis_result; NULL if analyzer skipped/failed
  analysis_approved      — set by save_analysis_result; NULL if analyzer skipped/failed
  analysis_reject_reason — set only when analysis_approved=FALSE; NULL otherwise
  event_type             — set by save_analysis_result; NULL if analyzer skipped/failed
  analysis_score         — set by save_analysis_result; NULL if analyzer skipped/failed
  post_text              — DEPRECATED (moved to x_posts); no longer written by the pipeline
  posted_at              — DEPRECATED (moved to x_posts); no longer written by the pipeline
  supervisor_attempts    — DEPRECATED (moved to x_posts); no longer written by the pipeline
  priority               — set by scraper (HTML badge); NULL if no priority badge
  x_post_id              — set by save_x_post; FK to x_posts.x_post_id; NULL until posted

x_posts table (one row per generated post; see _X_POSTS_SCHEMA):
  x_post_id, window, post_text, tweet_ids (PUL-27), posted_at, supervisor_attempts,
  x_publish_status (published|skipped|failed|partial; NULL for legacy/pre-publish rows)
"""
import calendar
import hashlib
import logging
import os
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Generous safety cap for the over-fetch in fetch_top_n_for_window — bounds
# SQL volume while giving select_top_companies enough rows to backfill slots.
_FETCH_SAFETY_CAP = 200

from google.cloud import bigquery  # noqa: E402
from google.cloud.exceptions import NotFound  # noqa: E402

from src.exceptions import BigQueryError  # noqa: E402
from src.post_selection import select_top_companies  # noqa: E402

_DATASET = os.environ.get("BIGQUERY_DATASET", "espi_ebi")
_TABLE_NAME = "announcements"
_ANNOUNCEMENTS_DEFAULT_DAYS = 90

_SCHEMA = [
    bigquery.SchemaField("announcement_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("url", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("published_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("title", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("company", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("ticker", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("post_text", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("posted_at", "TIMESTAMP", mode="NULLABLE"),
    bigquery.SchemaField("analyzed_at", "TIMESTAMP", mode="NULLABLE"),
    bigquery.SchemaField("supervisor_attempts", "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("parsed_content", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("priority", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("structured_analysis", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("analysis_approved", "BOOL", mode="NULLABLE"),
    bigquery.SchemaField("analysis_reject_reason", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("event_type", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("analysis_score", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("x_post_id", "STRING", mode="NULLABLE"),
]

_X_POSTS_TABLE_NAME = "x_posts"

_X_POSTS_SCHEMA = [
    bigquery.SchemaField("x_post_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("window", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("post_text", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("tweet_ids", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("posted_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("supervisor_attempts", "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("x_publish_status", "STRING", mode="NULLABLE"),
]

_client: bigquery.Client | None = None
_client_lock = threading.Lock()


def _get_client() -> bigquery.Client:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                import google.auth

                project = os.environ.get("GOOGLE_CLOUD_PROJECT")
                credentials, _ = google.auth.default()
                # Override ADC quota project to match the target project, avoiding
                # 403s when the ADC quota_project_id is set to a different project.
                # Guard: with_quota_project is not on all credential types (e.g. WIF).
                if hasattr(credentials, "with_quota_project"):
                    credentials = credentials.with_quota_project(project)
                else:
                    logger.warning(
                        "Credentials lack with_quota_project; quota project not overridden"
                        " — may cause 403 on WIF deployments"
                    )
                _client = bigquery.Client(project=project, credentials=credentials)
    return _client


def _table_ref(client: bigquery.Client, table: str = _TABLE_NAME) -> str:
    return f"{client.project}.{_DATASET}.{table}"


def announcement_id_for_url(url: str) -> str:
    """SHA256 hex digest of the announcement URL — stable dedup key."""
    return hashlib.sha256(url.encode()).hexdigest()


def _announcement_id(url: str) -> str:
    return announcement_id_for_url(url)


def create_table_if_not_exists() -> None:
    """Create the announcements table in BigQuery if it does not already exist."""
    client = _get_client()
    table_id = _table_ref(client)
    try:
        client.get_table(table_id)
        logger.info("BQ table already exists: %s", table_id)
    except NotFound:
        table = bigquery.Table(table_id, schema=_SCHEMA)
        table.time_partitioning = bigquery.TimePartitioning(field="published_at", type_="DAY")
        table.clustering_fields = ["ticker"]
        client.create_table(table)
        logger.info("BQ table created: %s", table_id)


def create_x_posts_table_if_not_exists() -> None:
    """Create the x_posts table in BigQuery if it does not already exist."""
    client = _get_client()
    table_id = _table_ref(client, _X_POSTS_TABLE_NAME)
    try:
        client.get_table(table_id)
        logger.info("BQ table already exists: %s", table_id)
    except NotFound:
        table = bigquery.Table(table_id, schema=_X_POSTS_SCHEMA)
        client.create_table(table)
        logger.info("BQ table created: %s", table_id)


def ensure_schema_current(
    table_name: str = _TABLE_NAME,
    schema: list[bigquery.SchemaField] | None = None,
) -> None:
    """Add any missing columns from `schema` to the existing BQ table `table_name`.

    Defaults to the announcements table + `_SCHEMA`. Pass `_X_POSTS_TABLE_NAME` /
    `_X_POSTS_SCHEMA` (via `ensure_x_posts_schema_current()`) to migrate the x_posts
    table through the same additive-column mechanism. Safe to call on every startup —
    no-op if the schema is already current. Raises BigQueryError if the update fails.
    """
    schema = schema if schema is not None else _SCHEMA
    client = _get_client()
    table_id = _table_ref(client, table_name)
    try:
        table = client.get_table(table_id)
    except NotFound:
        logger.info("BQ table %s not found — run create_*_if_not_exists() first", table_name)
        return
    existing_names = {f.name for f in table.schema}
    missing = [f for f in schema if f.name not in existing_names]
    if not missing:
        logger.info("BQ schema already current for %s", table_name)
        return
    table.schema = table.schema + missing
    try:
        client.update_table(table, ["schema"])
        logger.info(
            "BQ schema updated for %s: added columns %s",
            table_name, [f.name for f in missing],
        )
    except Exception as exc:
        raise BigQueryError(f"ensure_schema_current failed for {table_name}: {exc}") from exc


def ensure_x_posts_schema_current() -> None:
    """Migrate the x_posts table — add any missing `_X_POSTS_SCHEMA` columns.

    Thin binding over `ensure_schema_current()` for the x_posts table/schema; idempotent
    and safe to call on every post-job startup. A new x_posts column (e.g. PUL-26's
    `x_publish_status`) never lands in prod unless this runs at startup.
    """
    ensure_schema_current(_X_POSTS_TABLE_NAME, _X_POSTS_SCHEMA)


_PORTFOLIO_SNAPSHOTS_TABLE_NAME = "portfolio_snapshots"

_PORTFOLIO_SNAPSHOTS_SCHEMA = [
    bigquery.SchemaField("snapshot_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("wallet", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("snapshot_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("total_value", "FLOAT64", mode="REQUIRED"),
    bigquery.SchemaField("currency", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("day_change_abs", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("day_change_pct", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("positions_json", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
]


def create_portfolio_snapshots_table_if_not_exists() -> None:
    """Create the portfolio_snapshots table in BigQuery if it does not already exist."""
    client = _get_client()
    table_id = _table_ref(client, _PORTFOLIO_SNAPSHOTS_TABLE_NAME)
    try:
        client.get_table(table_id)
        logger.info("BQ table already exists: %s", table_id)
    except NotFound:
        table = bigquery.Table(table_id, schema=_PORTFOLIO_SNAPSHOTS_SCHEMA)
        client.create_table(table)
        logger.info("BQ table created: %s", table_id)


def ensure_portfolio_snapshots_schema_current() -> None:
    """Migrate the portfolio_snapshots table — add any missing schema columns.

    Thin binding over `ensure_schema_current()`; idempotent and safe to call on
    every skill invocation, matching the existing x_posts migration convention.
    """
    ensure_schema_current(_PORTFOLIO_SNAPSHOTS_TABLE_NAME, _PORTFOLIO_SNAPSHOTS_SCHEMA)


def save_portfolio_snapshot(
    wallet: str,
    snapshot_date: date,
    total_value: float,
    currency: str | None,
    day_change_abs: float | None,
    day_change_pct: float | None,
    positions_json: str | None,
) -> str:
    """Insert one portfolio_snapshots row (one wallet, one day) and return its snapshot_id.

    Raises BigQueryError if the query job fails.
    """
    client = _get_client()
    snapshot_id = uuid.uuid4().hex

    query = f"""
        INSERT INTO `{_table_ref(client, _PORTFOLIO_SNAPSHOTS_TABLE_NAME)}`
            (snapshot_id, wallet, snapshot_date, total_value, currency,
             day_change_abs, day_change_pct, positions_json, created_at)
        VALUES
            (@snapshot_id, @wallet, @snapshot_date, @total_value, @currency,
             @day_change_abs, @day_change_pct, @positions_json, CURRENT_TIMESTAMP())
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("snapshot_id", "STRING", snapshot_id),
            bigquery.ScalarQueryParameter("wallet", "STRING", wallet),
            bigquery.ScalarQueryParameter("snapshot_date", "DATE", snapshot_date),
            bigquery.ScalarQueryParameter("total_value", "FLOAT64", total_value),
            bigquery.ScalarQueryParameter("currency", "STRING", currency),
            bigquery.ScalarQueryParameter("day_change_abs", "FLOAT64", day_change_abs),
            bigquery.ScalarQueryParameter("day_change_pct", "FLOAT64", day_change_pct),
            bigquery.ScalarQueryParameter("positions_json", "STRING", positions_json),
        ]
    )
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"save_portfolio_snapshot failed: {exc}") from exc
    if job.errors:
        raise BigQueryError(f"save_portfolio_snapshot failed: {job.errors}")
    logger.debug("save_portfolio_snapshot: wallet=%s snapshot_date=%s id=%s", wallet, snapshot_date, snapshot_id)
    return snapshot_id


def get_latest_snapshot_before(wallet: str, before_date: date) -> dict | None:
    """Return the most recent portfolio_snapshots row for `wallet` strictly before `before_date`.

    Returns None if no prior row exists (first-ever run for that wallet).
    Raises BigQueryError on query failure.
    """
    client = _get_client()
    _t = time.time()
    query = f"""
        SELECT snapshot_id, wallet, snapshot_date, total_value, currency,
               day_change_abs, day_change_pct, positions_json
        FROM `{_table_ref(client, _PORTFOLIO_SNAPSHOTS_TABLE_NAME)}`
        WHERE wallet = @wallet AND snapshot_date < @before_date
        ORDER BY snapshot_date DESC
        LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("wallet", "STRING", wallet),
            bigquery.ScalarQueryParameter("before_date", "DATE", before_date),
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"get_latest_snapshot_before failed: {exc}") from exc
    if not rows:
        logger.debug("BQ get_latest_snapshot_before: %.0fms", (time.time() - _t) * 1000)
        return None
    row = rows[0]
    logger.debug("BQ get_latest_snapshot_before: %.0fms", (time.time() - _t) * 1000)
    return {
        "snapshot_id": row.snapshot_id,
        "wallet": row.wallet,
        "snapshot_date": row.snapshot_date,
        "total_value": row.total_value,
        "currency": row.currency,
        "day_change_abs": row.day_change_abs,
        "day_change_pct": row.day_change_pct,
        "positions_json": row.positions_json,
    }


def get_latest_snapshot_for_wallet(wallet: str) -> dict | None:
    """Return the most recently uploaded portfolio_snapshots row for `wallet`.

    Returns None if that wallet has no rows. Raises BigQueryError on query failure.
    """
    client = _get_client()
    _t = time.time()
    query = f"""
        SELECT snapshot_id, wallet, snapshot_date, total_value, currency,
               day_change_abs, day_change_pct, positions_json
        FROM `{_table_ref(client, _PORTFOLIO_SNAPSHOTS_TABLE_NAME)}`
        WHERE wallet = @wallet
        ORDER BY snapshot_date DESC, created_at DESC
        LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("wallet", "STRING", wallet),
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"get_latest_snapshot_for_wallet failed: {exc}") from exc
    if not rows:
        logger.debug("BQ get_latest_snapshot_for_wallet: %.0fms", (time.time() - _t) * 1000)
        return None
    row = rows[0]
    logger.debug("BQ get_latest_snapshot_for_wallet: %.0fms", (time.time() - _t) * 1000)
    return {
        "snapshot_id": row.snapshot_id,
        "wallet": row.wallet,
        "snapshot_date": row.snapshot_date,
        "total_value": row.total_value,
        "currency": row.currency,
        "day_change_abs": row.day_change_abs,
        "day_change_pct": row.day_change_pct,
        "positions_json": row.positions_json,
    }


def _log_unexplained_holdings(
    fn: str, user_id: str, portfolio_id: str | None, residual: int | None
) -> None:
    """Report holdings the broker operations do not account for, at a level prod sees.

    Phase 4 of PUL-103 asked for this count so an unexplained residual is noticed by
    us rather than by the user. The count itself rides in the DEBUG timing line, but
    `api_main.py` configures the root logger at INFO, so DEBUG never leaves the
    process in Cloud Run — the diagnostic would have been dead exactly where it was
    meant to live.

    Zero is the normal case and stays silent, so this costs nothing in log volume and
    fires only when there is something to look at. Cash is already excluded by the
    query; what reaches here is a real position no operation explains.
    """
    if not residual:
        return
    logger.info(
        "%s: %s holding(s) not explained by broker operations (user=%s, portfolio=%s)",
        fn,
        residual,
        user_id,
        portfolio_id if portfolio_id is not None else "all",
    )


def get_portfolio_calendar_data(
    portfolio_id: str | None,
    user_id: str,
    year: int,
    month: int,
) -> list[dict]:
    """Return daily portfolio values for a given month, valued at the shares held that day.

    When portfolio_id is provided, results are scoped to that wallet.  When it is
    None, the CTEs span *all* of the user's wallets, so the daily SUM(...) becomes
    the combined-across-all-portfolios value/change (the "Wszystkie" view).

    Returns one dict per trading day with keys: snapshot_date (date),
    portfolio_value (float, best-effort sum), daily_change_pln (float,
    SUM(shares_on_day × zmiana_kwotowa)), prices_found (int), total_positions (int).
    Returns [] when the portfolio holds nothing in the window.  Raises BigQueryError
    on failure.

    Holdings are time-aware (PUL-103).  Until then the query crossed every trading
    day with the *current* positions snapshot, so it reported daily P&L for dates the
    portfolio did not hold what it holds today — including dates before it existed at
    all.  The share count is now reconstructed as a **backward correction over the
    snapshot**::

        shares(day) = today_shares − Σ(±volume of operations later than that day)

    written as a difference of cumulative sums over ``user_broker_operations``.  That
    direction is deliberate and load-bearing.  The operations table is a complete
    record of *movements the broker saw*, not of holdings: cash carries no operation
    at all, positions added by hand carry none, and in-kind distributions (spin-offs)
    are structurally absent from the XTB export.  Rebuilding forward from operations
    would erase all three.  Correcting backwards leaves whatever the operations do
    not explain — the *residual* — constant across the window instead, which is the
    honest answer for those tickers, and makes the last day equal today's stored
    share count by construction (the PUL-100 right-edge invariant).

    The same formula absorbs three more cases without a branch: a ticker sold to zero
    reappears in history even though its position row was deleted at import, an
    export window that starts after a purchase yields a positive residual rather than
    negative shares, and any operation vocabulary we learn to parse later simply
    shrinks the residual.

    Two traps the shape guards against.  The cumulative sums span the operation
    history with **no horizon** — a window function over the trading-day spine would
    stop at the end of the month being viewed, so June would still count shares
    bought in December.  And operations are matched with a range condition, never by
    date equality, because operation days need not be trading days (measured: 16 of
    426).

    The series is also bounded at the wallet's inception — the first *share-affecting*
    operation, or, for a wallet with no operations at all, the day it was created.
    Days before it emit no row, so the calendar renders them blank, exactly like days
    that have not arrived yet.  A zero would be a different lie: it reads as a real
    flat session.  Deliberately not the first operation of any kind, because on every
    real wallet that is a deposit, and a deposit day holds nothing but the cash
    residual — which prices at 1.00 with a zero move and renders as a green "+0 PLN".
    Accepted consequence: a wallet that has only ever received deposits, without a
    single purchase, gets an empty calendar.  There was nothing to value.

    The bound trims the left edge only.  A wallet that later sold everything and sat
    in cash still reports those days, at a zero move — which is true, it really was
    flat — but the cash figure shown is *today's* balance, not that day's, because
    cash carries no operation and is therefore pure residual.  Reconstructing it is
    not possible from what is stored: summing amount_pln over the imported operations
    misses the foreign trades the parser drops on purpose (measured on the real
    export: 84.03 from the broker's own total against 143.94 from the parsed rows).

    A 10-day price lookback is still scanned, for an unrelated reason: it gives the
    close carry-forward a predecessor for the 1st of the month.  Those rows are
    filtered out before the result is returned.
    """
    client = _get_client()
    _t = time.time()
    month_start = date(year, month, 1)
    # Scanned only so the carry-forward below has a predecessor for the 1st of the
    # month; rows before month_start are filtered out of the result.
    lookback_start = month_start - timedelta(days=10)
    _, last_day = calendar.monthrange(year, month)
    end_date = date(year, month, last_day)

    cds_ref = _table_ref(client, _COMPANY_DAILY_STATS_TABLE_NAME)
    etf_ref = _table_ref(client, _ETF_QUOTES_TABLE_NAME)
    pos_ref = _table_ref(client, _USER_PORTFOLIO_POSITIONS_TABLE_NAME)
    ops_ref = _table_ref(client, _USER_BROKER_OPERATIONS_TABLE_NAME)
    pfs_ref = _table_ref(client, _USER_PORTFOLIOS_TABLE_NAME)
    portfolio_filter = "AND portfolio_id = @portfolio_id" if portfolio_id is not None else ""

    query = f"""
        WITH
          trading_days AS (
            SELECT DISTINCT snapshot_date
            FROM `{cds_ref}`
            WHERE snapshot_date BETWEEN @lookback_start AND @end_date
          ),
          px_cds AS (
            -- Deduplicated per (ticker, date): the calendar joined the price tables
            -- raw, so a duplicate row fanned out and double-counted the day.  The
            -- value chart has carried this guard since PUL-100.
            -- A real close outranks a fresher fetch: since PUL-98 the official feed
            -- honestly reports a session with no trades, so the newest row for a
            -- (ticker, date) pair can carry no price at all.  Ordering on fetched_at
            -- alone would then discard the real number in favour of the NULL.  The
            -- value chart sidesteps this by filtering NULLs before deduping; the
            -- calendar cannot copy that, because it also needs zmiana_kwotowa.
            SELECT ticker, snapshot_date, kurs_zamkniecia, zmiana_kwotowa
            FROM `{cds_ref}`
            WHERE snapshot_date BETWEEN @lookback_start AND @end_date
            QUALIFY ROW_NUMBER() OVER (
              PARTITION BY ticker, snapshot_date
              ORDER BY (kurs_zamkniecia IS NOT NULL) DESC, fetched_at DESC
            ) = 1
          ),
          px_etf AS (
            SELECT ticker, snapshot_date, kurs_zamkniecia, zmiana_kwotowa
            FROM `{etf_ref}`
            WHERE snapshot_date BETWEEN @lookback_start AND @end_date
            QUALIFY ROW_NUMBER() OVER (
              PARTITION BY ticker, snapshot_date
              ORDER BY (kurs_zamkniecia IS NOT NULL) DESC, fetched_at DESC
            ) = 1
          ),
          positions AS (
            SELECT portfolio_id, ticker, shares
            FROM `{pos_ref}`
            WHERE user_id = @user_id {portfolio_filter}
          ),
          ops_daily AS (
            -- Aggregated to one row per (wallet, ticker, day) BEFORE any join, so a
            -- second operation on the same day cannot multiply the day's holdings.
            -- Direction comes from op_type alone: volume is always positive, and a
            -- sale's own comment reads "CLOSE BUY 5 @ 55.00".
            SELECT
              portfolio_id, ticker,
              DATE(occurred_at, 'Europe/Warsaw') AS op_date,
              SUM(CASE WHEN op_type = 'buy'  THEN volume
                       WHEN op_type = 'sell' THEN -volume
                       ELSE 0 END) AS signed_volume
            FROM `{ops_ref}`
            WHERE user_id = @user_id {portfolio_filter}
              AND ticker IS NOT NULL
              AND op_type IN ('buy', 'sell')
            GROUP BY portfolio_id, ticker, op_date
          ),
          ops_totals AS (
            -- The whole history, with no horizon: this is what the reconstruction
            -- converges to, and subtracting a bounded sum from it is what makes the
            -- window-free formula work.
            SELECT portfolio_id, ticker, SUM(signed_volume) AS total_signed
            FROM ops_daily
            GROUP BY portfolio_id, ticker
          ),
          inception AS (
            -- The earliest day this wallet can honestly report.  Deliberately the
            -- first *share-affecting* operation, not the first operation of any kind:
            -- on every real wallet the first row is a deposit, and a deposit day
            -- holds nothing but the cash residual — priced at 1.00 with a zero move,
            -- which renders as a green "+0 PLN" cell, i.e. a real flat day that never
            -- happened.  ops_daily is already narrowed to buy/sell and scoped to the
            -- wallet (or to all of them in "Wszystkie" mode).
            --
            -- A wallet with no operations at all falls back to when it was created.
            -- user_portfolio_positions.created_at is NOT usable here: it records the
            -- import, not the purchase (every imported position carries the same
            -- 2026-07-29/30 stamp).
            SELECT COALESCE(
              (SELECT MIN(op_date) FROM ops_daily),
              (SELECT MIN(DATE(created_at, 'Europe/Warsaw')) FROM `{pfs_ref}`
               WHERE user_id = @user_id {portfolio_filter})
            ) AS first_day
          ),
          holders AS (
            -- Positions ∪ operations.  Positions alone would lose a ticker sold to
            -- zero (its row is deleted at import); operations alone would lose cash,
            -- hand-entered positions and in-kind distributions.
            SELECT
              COALESCE(p.portfolio_id, t.portfolio_id) AS portfolio_id,
              COALESCE(p.ticker, t.ticker)             AS ticker,
              COALESCE(p.shares, 0)                    AS today_shares,
              COALESCE(t.total_signed, 0)              AS total_signed
            --
            -- portfolio_id is NULLABLE on positions (orphan rows predating PUL-64),
            -- and a plain `=` never matches NULL, so such a position does not pair
            -- with that ticker's operations — it becomes its own holder row and the
            -- operations become another.  Deliberate: nothing says the two are the
            -- same holding, and a NULL-safe key would only assert that they are.
            -- The ops-only row reconstructs negative before its first buy and is
            -- dropped by the positive threshold below, leaving the orphan position
            -- held constant — which is exactly the residual semantics.
            FROM positions p
            FULL OUTER JOIN ops_totals t
              ON t.portfolio_id = p.portfolio_id AND t.ticker = p.ticker
          ),
          holdings AS (
            -- shares(day) = today − (everything ever − everything up to and including
            -- that day).  The join is a RANGE condition, never date equality: an
            -- operation may fall on a day the exchange did not trade.
            SELECT
              td.snapshot_date,
              h.portfolio_id,
              h.ticker,
              h.today_shares - (h.total_signed - COALESCE(SUM(o.signed_volume), 0))
                AS shares_on_day
            FROM trading_days td
            CROSS JOIN holders h
            LEFT JOIN ops_daily o
              ON  o.portfolio_id = h.portfolio_id
              AND o.ticker       = h.ticker
              AND o.op_date     <= td.snapshot_date
            GROUP BY td.snapshot_date, h.portfolio_id, h.ticker, h.today_shares, h.total_signed
          ),
          daily_prices AS (
            SELECT
              hd.snapshot_date,
              hd.portfolio_id,
              hd.ticker,
              hd.shares_on_day AS shares,
              IF(hd.ticker = '{CASH_TICKER}', 1.0,
                 COALESCE(cds.kurs_zamkniecia, etq.kurs_zamkniecia)) AS close_price,
              IF(hd.ticker = '{CASH_TICKER}', 0.0,
                 COALESCE(cds.zmiana_kwotowa, etq.zmiana_kwotowa))  AS daily_chg
            FROM holdings hd
            LEFT JOIN px_cds cds
              ON cds.ticker = hd.ticker AND cds.snapshot_date = hd.snapshot_date
            LEFT JOIN px_etf etq
              ON etq.ticker = hd.ticker AND etq.snapshot_date = hd.snapshot_date
            -- Float volumes leave ~1e-13 behind on a fully sold lot; without the
            -- threshold that dust renders as a phantom holding and inflates both
            -- total_positions and prices_found.
            --
            -- Strictly positive, never ABS(): a negative reconstruction means the
            -- snapshot and the operations disagree in the one direction the residual
            -- cannot absorb — buys on record with no position row, which is what
            -- deleting a position by hand leaves behind (the delete does not touch
            -- user_broker_operations).  Letting that through would subtract it from
            -- the day's value and paint a large red loss.  Dropping it says "we do
            -- not know what was held", which is the truth.  The oversell case is
            -- unaffected: it reconstructs positive (3 − (−5) = 8).
            WHERE hd.shares_on_day > 1e-9
          ),
          filled AS (
            -- A session with no trades leaves no close.  Bankier always published
            -- the last known number so this could not happen before PUL-98; the
            -- official feed reports the session honestly (measured 2026-07-28: 100
            -- of 332 NewConnect rows carry no close).  Scoring that as zero would
            -- drop the whole position out of the day's value and render as a real
            -- loss, so carry the last known close forward exactly as
            -- get_portfolio_history does.  daily_chg is deliberately NOT carried
            -- forward: a day without trades had no move.
            SELECT
              snapshot_date, ticker, shares, daily_chg,
              LAST_VALUE(close_price IGNORE NULLS) OVER (
                PARTITION BY portfolio_id, ticker ORDER BY snapshot_date
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
              ) AS close_ff
            FROM daily_prices
          ),
          daily_portfolio AS (
            SELECT
              snapshot_date,
              SUM(CASE WHEN close_ff IS NOT NULL THEN shares * close_ff ELSE 0 END)
                AS portfolio_value,
              SUM(CASE WHEN daily_chg IS NOT NULL THEN shares * daily_chg ELSE 0 END)
                AS daily_change_pln,
              COUNTIF(close_ff IS NOT NULL) AS prices_found,
              COUNT(*) AS total_positions
            FROM filled
            WHERE snapshot_date >= @month_start
              -- Before inception the wallet emits no row at all, so the day renders
              -- as no_data (white) — the same as a day that has not arrived yet.  A
              -- zero would be a different lie: it reads as a real flat session.
              -- COALESCE keeps a wallet whose inception cannot be determined (an
              -- orphan position with no portfolio_id) reporting rather than blank.
              AND snapshot_date >= COALESCE((SELECT first_day FROM inception), snapshot_date)
            GROUP BY snapshot_date
          )
        SELECT snapshot_date, portfolio_value, daily_change_pln, prices_found, total_positions,
               -- Diagnostic only, never returned: how many holdings the operations do
               -- NOT explain.  Absorbing a residual is the correct behaviour, but an
               -- unexpected one means the reconstruction lost something, and the first
               -- person to notice should not be the user looking at a wrong number.
               -- Cash is excluded because it is residual by construction.
               (SELECT COUNT(*) FROM holders
                WHERE ticker != '{CASH_TICKER}'
                  AND ABS(today_shares - total_signed) > 1e-9) AS residual_holders
        FROM daily_portfolio
        ORDER BY snapshot_date
    """
    params: list[bigquery.ScalarQueryParameter] = [
        bigquery.ScalarQueryParameter("user_id",        "STRING", user_id),
        bigquery.ScalarQueryParameter("lookback_start", "DATE",   lookback_start),
        bigquery.ScalarQueryParameter("month_start",    "DATE",   month_start),
        bigquery.ScalarQueryParameter("end_date",       "DATE",   end_date),
    ]
    if portfolio_id is not None:
        params.append(bigquery.ScalarQueryParameter("portfolio_id", "STRING", portfolio_id))
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"get_portfolio_calendar_data failed: {exc}") from exc
    _residual = getattr(rows[0], "residual_holders", None) if rows else 0
    logger.debug(
        "BQ get_portfolio_calendar_data: %.0fms, unexplained holdings: %s",
        (time.time() - _t) * 1000,
        _residual,
    )
    _log_unexplained_holdings("get_portfolio_calendar_data", user_id, portfolio_id, _residual)
    return [
        {
            "snapshot_date": row.snapshot_date,
            "portfolio_value": float(row.portfolio_value),
            "daily_change_pln": float(row.daily_change_pln),
            "prices_found": int(row.prices_found),
            "total_positions": int(row.total_positions),
        }
        for row in rows
    ]


def get_portfolio_inception(portfolio_id: str | None, user_id: str) -> date | None:
    """Return the day a wallet began: its first *share-affecting* operation, or —
    for a wallet that has none — the day it was created.

    Same definition as the ``inception`` CTE inside get_portfolio_calendar_data and
    get_portfolio_history, deliberately: three variants of "when did this start"
    would drift, and the calendar's left edge and the picker's lower bound have to
    agree about which months exist at all.

    Deliberately not the first operation of *any* kind — on every real wallet that
    is a deposit, and a deposit day holds nothing but cash.

    ``portfolio_id=None`` spans all of the user's wallets (the "Wszystkie" view), so
    the answer is the earliest inception among them.  Returns None when the user has
    neither operations nor wallets.  Raises BigQueryError on failure.

    The wallet's own ``created_at`` is only the fallback, never the bound: the broker
    import backfills operations far older than the wallet row, so a wallet created
    last month legitimately holds years of history.
    """
    client = _get_client()
    ops_ref = _table_ref(client, _USER_BROKER_OPERATIONS_TABLE_NAME)
    pfs_ref = _table_ref(client, _USER_PORTFOLIOS_TABLE_NAME)
    portfolio_filter = "AND portfolio_id = @portfolio_id" if portfolio_id is not None else ""

    query = f"""
        SELECT COALESCE(
          (SELECT MIN(DATE(occurred_at, 'Europe/Warsaw'))
           FROM `{ops_ref}`
           WHERE user_id = @user_id {portfolio_filter}
             AND ticker IS NOT NULL
             AND op_type IN ('buy', 'sell')),
          (SELECT MIN(DATE(created_at, 'Europe/Warsaw'))
           FROM `{pfs_ref}`
           WHERE user_id = @user_id {portfolio_filter})
        ) AS first_day
    """
    params = [bigquery.ScalarQueryParameter("user_id", "STRING", user_id)]
    if portfolio_id is not None:
        params.append(bigquery.ScalarQueryParameter("portfolio_id", "STRING", portfolio_id))
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"get_portfolio_inception failed: {exc}") from exc
    # An aggregate always returns one row; the value inside it is NULL when there is
    # nothing to date, so the emptiness test has to be on the value, not the list.
    return rows[0].first_day if rows else None


def get_portfolio_history(
    portfolio_id: str | None,
    user_id: str,
    start_date: date,
) -> list[dict]:
    """Return the daily portfolio value + cumulative unrealized P&L series over a range.

    When portfolio_id is provided, results are scoped to that wallet.  When it is None,
    the positions CTE spans *all* of the user's wallets, so the series is the combined
    value/P&L across every portfolio (the "Wszystkie" view).

    One row per trading day in [start_date, CURRENT_DATE()], ascending by date, with keys:
    snapshot_date (date), value_pln (float), pnl_pln (float).  value_pln is the shares held
    *that day* valued at that day's close; pnl_pln = value_pln − Σ(shares × avg_buy_price).

    Prices are filled in **both** directions.  Forward (LOCF, PUL-79 F1): each held ticker
    carries its last known close across trading days, so a missing daily close no longer
    collapses a position's value to 0.  Backward (BOCF, PUL-100): days *before* a ticker's
    first quote carry its earliest known close.  Without BOCF the old full-coverage gate
    started the series at the latest first-price date across all holdings, so a sub-1%
    position in a freshly listed company (S2B, listed 2026-04-16) truncated a whole year of
    history to ~3 months.  The price scan reaches ~400 days before start_date.

    After both fills, ``px_ff IS NULL`` is **all-or-nothing per ticker** — it can only mean
    the ticker has no price anywhere in the scan window.  That is what makes the per-day
    conditional aggregation safe: such a ticker is dropped on *every* day, so dropping it
    cannot introduce a step in the curve.  Its cost basis is dropped alongside its value, or
    P&L would carry a permanent phantom loss equal to its purchase cost.  The surviving gate
    (``covered > 0``) fires only when *nothing* in the portfolio is priced.

    Holdings are time-aware (PUL-103).  The share count on each day is a **backward
    correction over the current snapshot** — ``today_shares − Σ(±volume of operations
    later than that day)`` — exactly as get_portfolio_calendar_data computes it; the
    long comments on the shared CTEs live there.  Until PUL-103 this query crossed the
    spine with the *current* positions, so a lot bought last month contributed its full
    weight to every day of last year, and a wallet reported a curve for dates it did
    not exist.  The series is now bounded at ``GREATEST(start_date, inception)``.

    Three corrections act on the same rows and are deliberately kept disjoint:
    ``shares_on_day`` decides *whether* a ticker is held, LOCF/BOCF decides *at what
    price*, and ``covered > 0`` decides whether anything at all could be priced.  The
    dust threshold is what keeps them apart — a zero-share row surviving into the fill
    would carry a non-NULL px_ff, so COUNTIF would stop measuring "nothing could be
    priced" and start measuring "the universe was empty", and ``notes`` would announce
    the debut of a ticker nobody held in this window.

    A day before inception is dropped by the **bound**, never by the gate: the bound is
    evidence that the wallet did not exist, the gate is evidence that the data failed.

    Accepted approximations.  The share counts are no longer among them, but the cost
    basis still is: ``avg_buy_price`` is one time-blind weighted average, so P&L now has
    correct weights on a basis that does not move (FIFO-as-of-date needs Python, not a
    SQL window — measured 284.28 weighted against 297.90 FIFO on SNT).  A ticker whose
    position row no longer exists — sold to zero, its row deleted at import — has no
    stored basis at all, so its buys' weighted unit price stands in; that keeps
    ``pnl = value − basis`` true instead of letting the ticker's whole value read as
    profit.  And backward-filling still contributes a constant
    ``shares × (first_px − avg_buy_price)`` to every pre-debut day of a *residual*
    ticker, so a holder who bought above the debut price sees a flat phantom loss across
    that leg (PUL-100).  BOCF's reach narrows sharply here: a ticker with operations
    holds zero shares before its first buy, so the backward fill multiplies by nothing.

    Returns ``{"series": [...], "notes": [...], "excluded": [...], "data_from": ...}``:

    * ``series`` — one entry per trading day in [start_date, CURRENT_DATE()], ascending, with
      keys snapshot_date (date), value_pln (float), pnl_pln (float).
    * ``notes``  — holdings whose first available price falls *after* start_date, i.e. those
      the backward-fill actually affected in this window: ticker, listed_from (date), price.
      ``listed_from`` is the first date **we have data for**, which is not necessarily a
      listing date — a ticker whose history starts when the scraper did looks identical here.
    * ``excluded`` — tickers with no price anywhere in the window, left out of the valuation.
    * ``data_from`` — the day the series actually starts when inception falls inside the
      requested range, else None.  The chart's X axis is index-based, so two months of
      history inside a 1y range is visually indistinguishable from a full year.

    Metadata is carried by the same query (a second round trip would double a ~1.6 s
    user-facing latency) and the join is written meta-first, so the lists still reach the
    caller when *no* day survives the gate.  Raises BigQueryError on failure.
    """
    client = _get_client()
    _t = time.time()

    cds_ref = _table_ref(client, _COMPANY_DAILY_STATS_TABLE_NAME)
    etf_ref = _table_ref(client, _ETF_QUOTES_TABLE_NAME)
    pos_ref = _table_ref(client, _USER_PORTFOLIO_POSITIONS_TABLE_NAME)
    ops_ref = _table_ref(client, _USER_BROKER_OPERATIONS_TABLE_NAME)
    pfs_ref = _table_ref(client, _USER_PORTFOLIOS_TABLE_NAME)
    portfolio_filter = "AND portfolio_id = @portfolio_id" if portfolio_id is not None else ""

    query = f"""
        WITH
          positions AS (
            SELECT portfolio_id, ticker, shares, avg_buy_price
            FROM `{pos_ref}`
            WHERE user_id = @user_id {portfolio_filter}
          ),
          ops_daily AS (
            SELECT
              portfolio_id, ticker,
              DATE(occurred_at, 'Europe/Warsaw') AS op_date,
              SUM(CASE WHEN op_type = 'buy'  THEN volume
                       WHEN op_type = 'sell' THEN -volume
                       ELSE 0 END) AS signed_volume
            FROM `{ops_ref}`
            WHERE user_id = @user_id {portfolio_filter}
              AND ticker IS NOT NULL
              AND op_type IN ('buy', 'sell')
            GROUP BY portfolio_id, ticker, op_date
          ),
          ops_totals AS (
            SELECT portfolio_id, ticker, SUM(signed_volume) AS total_signed
            FROM ops_daily
            GROUP BY portfolio_id, ticker
          ),
          ops_basis AS (
            -- Stand-in cost basis for a ticker with no position row left (sold to zero;
            -- the import deletes the row).  Without it such a ticker adds value to the
            -- historical days it was held while adding no basis, and the curve shows a
            -- phantom profit that unwinds on the sale date.  Same class of number as
            -- avg_buy_price — a time-blind weighted average — so it changes nothing
            -- about the approximation, only about which tickers have one.
            SELECT portfolio_id, ticker,
                   SAFE_DIVIDE(SUM(volume * unit_price), SUM(volume)) AS avg_op_price
            FROM `{ops_ref}`
            WHERE user_id = @user_id {portfolio_filter}
              AND ticker IS NOT NULL AND op_type = 'buy'
              AND unit_price IS NOT NULL AND volume > 0
            GROUP BY portfolio_id, ticker
          ),
          inception AS (
            -- The first share-affecting operation, or — for a wallet that has none —
            -- the day the wallet was created.  See get_portfolio_calendar_data for why
            -- it is not the first operation of any kind.
            SELECT COALESCE(
              (SELECT MIN(op_date) FROM ops_daily),
              (SELECT MIN(DATE(created_at, 'Europe/Warsaw')) FROM `{pfs_ref}`
               WHERE user_id = @user_id {portfolio_filter})
            ) AS first_day
          ),
          holders AS (
            SELECT
              COALESCE(p.portfolio_id, t.portfolio_id) AS portfolio_id,
              COALESCE(p.ticker, t.ticker)             AS ticker,
              COALESCE(p.shares, 0)                    AS today_shares,
              COALESCE(t.total_signed, 0)              AS total_signed,
              COALESCE(p.avg_buy_price, b.avg_op_price) AS avg_price
            FROM positions p
            FULL OUTER JOIN ops_totals t
              ON t.portfolio_id = p.portfolio_id AND t.ticker = p.ticker
            LEFT JOIN ops_basis b
              ON  b.portfolio_id = COALESCE(p.portfolio_id, t.portfolio_id)
              AND b.ticker       = COALESCE(p.ticker, t.ticker)
          ),
          spine AS (
            SELECT DISTINCT snapshot_date
            FROM `{cds_ref}`
            WHERE snapshot_date BETWEEN DATE_SUB(@start_date, INTERVAL 400 DAY) AND CURRENT_DATE()
          ),
          px_raw AS (
            SELECT ticker, snapshot_date, kurs_zamkniecia AS px, 0 AS src
            FROM `{cds_ref}`
            WHERE snapshot_date BETWEEN DATE_SUB(@start_date, INTERVAL 400 DAY) AND CURRENT_DATE()
              AND kurs_zamkniecia IS NOT NULL
            UNION ALL
            SELECT ticker, snapshot_date, kurs_zamkniecia AS px, 1 AS src
            FROM `{etf_ref}`
            WHERE snapshot_date BETWEEN DATE_SUB(@start_date, INTERVAL 400 DAY) AND CURRENT_DATE()
              AND kurs_zamkniecia IS NOT NULL
          ),
          px_with_cash AS (
            -- Cash has no market-data row and never will, so without this branch
            -- the coverage gate treats it as an unpriced holding and drops both
            -- its value and its basis from every day of the curve.
            SELECT ticker, snapshot_date, px, src FROM px_raw
            UNION ALL
            SELECT '{CASH_TICKER}', snapshot_date, 1.0, 2 FROM spine
          ),
          px_dedup AS (
            SELECT ticker, snapshot_date, px
            FROM px_with_cash
            QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker, snapshot_date ORDER BY src) = 1
          ),
          filled AS (
            -- The price fill is computed per ticker over the WHOLE spine, independently
            -- of how many shares were held.  It has to be: the 400-day pre-roll exists
            -- to give LOCF a predecessor, and if the grid were filtered to days the
            -- ticker was actually held, the day it was bought would have no predecessor
            -- left and would fall through to BOCF — a *later* price standing in for an
            -- earlier one, which is the opposite of what the forward fill is for.
            SELECT
              s.snapshot_date, u.ticker,
              COALESCE(
                LAST_VALUE(d.px IGNORE NULLS) OVER (
                  PARTITION BY u.ticker ORDER BY s.snapshot_date
                  ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ),
                FIRST_VALUE(d.px IGNORE NULLS) OVER (
                  PARTITION BY u.ticker ORDER BY s.snapshot_date
                  ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING
                )
              ) AS px_ff
            FROM spine s
            CROSS JOIN (SELECT DISTINCT ticker FROM holders) u
            LEFT JOIN px_dedup d ON d.ticker = u.ticker AND d.snapshot_date = s.snapshot_date
          ),
          held AS (
            -- shares(day) = today − (everything ever − everything up to and including
            -- that day), matched by RANGE because an operation day need not be a
            -- trading day.  Restricted to the requested window: the pre-roll days are
            -- only ever needed by the price fill above.
            SELECT snapshot_date, ticker, avg_price, shares_on_day
            FROM (
              SELECT
                s.snapshot_date,
                h.ticker,
                h.avg_price,
                h.today_shares - (h.total_signed - COALESCE(SUM(o.signed_volume), 0))
                  AS shares_on_day
              FROM spine s
              CROSS JOIN holders h
              LEFT JOIN ops_daily o
                ON  o.portfolio_id = h.portfolio_id
                AND o.ticker       = h.ticker
                AND o.op_date     <= s.snapshot_date
              WHERE s.snapshot_date BETWEEN @start_date AND CURRENT_DATE()
              GROUP BY s.snapshot_date, h.portfolio_id, h.ticker, h.avg_price,
                       h.today_shares, h.total_signed
            )
            -- Strictly positive, never ABS(): float volumes leave ~1e-13 behind on a
            -- fully sold lot, and a genuinely negative reconstruction (buys on record
            -- with no position row, which deleting a position by hand leaves behind)
            -- would subtract from the day's value and paint a large red loss.
            WHERE shares_on_day > 1e-9
          ),
          coverage AS (
            -- Over the tickers actually held in this window, not over today's position
            -- list: a note about a debut nobody was exposed to is noise, and a ticker
            -- held only outside the window has no business in `excluded` either.
            SELECT
              u.ticker,
              MIN(d.snapshot_date) AS first_px_date,
              ARRAY_AGG(d.px IGNORE NULLS ORDER BY d.snapshot_date LIMIT 1)[SAFE_OFFSET(0)]
                AS first_px
            FROM (SELECT DISTINCT ticker FROM held) u
            LEFT JOIN px_dedup d ON d.ticker = u.ticker
            GROUP BY u.ticker
          ),
          meta AS (
            SELECT
              ARRAY(
                SELECT AS STRUCT ticker, first_px_date, first_px
                FROM coverage WHERE first_px_date > @start_date
              ) AS notes,
              ARRAY(SELECT ticker FROM coverage WHERE first_px_date IS NULL) AS excluded,
              (SELECT IF(first_day > @start_date, first_day, NULL) FROM inception)
                AS data_from,
              -- Diagnostic only, never returned — see get_portfolio_calendar_data.
              -- Carried by meta rather than by the series so it survives a window in
              -- which no day passes the gate.
              (SELECT COUNT(*) FROM holders
               WHERE ticker != '{CASH_TICKER}'
                 AND ABS(today_shares - total_signed) > 1e-9) AS residual_holders
          ),
          valued AS (
            SELECT h.snapshot_date, h.shares_on_day, h.avg_price, f.px_ff
            FROM held h
            LEFT JOIN filled f
              ON f.ticker = h.ticker AND f.snapshot_date = h.snapshot_date
          ),
          daily AS (
            SELECT
              snapshot_date,
              SUM(IF(px_ff IS NOT NULL, shares_on_day * px_ff, 0)) AS value_pln,
              SUM(IF(px_ff IS NOT NULL, shares_on_day * (px_ff - avg_price), 0)) AS pnl_pln,
              COUNTIF(px_ff IS NOT NULL) AS covered
            FROM valued
            -- GREATEST(@start_date, inception): `held` is already clamped to the range,
            -- so this is the inception half.  COALESCE keeps a wallet whose inception
            -- cannot be determined reporting rather than blank.
            WHERE snapshot_date >= COALESCE((SELECT first_day FROM inception), @start_date)
            GROUP BY snapshot_date
          )
        SELECT d.snapshot_date, d.value_pln, d.pnl_pln, m.notes, m.excluded, m.data_from,
               m.residual_holders
        FROM meta m
        LEFT JOIN daily d ON d.covered > 0
        ORDER BY d.snapshot_date
    """
    params: list[bigquery.ScalarQueryParameter] = [
        bigquery.ScalarQueryParameter("user_id",    "STRING", user_id),
        bigquery.ScalarQueryParameter("start_date", "DATE",   start_date),
    ]
    if portfolio_id is not None:
        params.append(bigquery.ScalarQueryParameter("portfolio_id", "STRING", portfolio_id))
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"get_portfolio_history failed: {exc}") from exc
    _residual = getattr(rows[0], "residual_holders", None) if rows else 0
    logger.debug(
        "BQ get_portfolio_history: %.0fms, unexplained holdings: %s",
        (time.time() - _t) * 1000,
        _residual,
    )
    _log_unexplained_holdings("get_portfolio_history", user_id, portfolio_id, _residual)
    # The meta-first join emits one metadata-only row (NULL date) when no day survives the
    # gate — carry its lists, but never let it become a data point.
    series = [
        {
            "snapshot_date": row.snapshot_date,
            "value_pln": float(row.value_pln),
            "pnl_pln": float(row.pnl_pln),
        }
        for row in rows
        if row.snapshot_date is not None
    ]
    first = rows[0] if rows else None
    notes = [
        {
            "ticker": note["ticker"],
            "listed_from": note["first_px_date"],
            "price": float(note["first_px"]),
        }
        for note in (first.notes or [])
    ] if first else []
    excluded = list(first.excluded or []) if first else []
    data_from = first.data_from if first else None
    return {"series": series, "notes": notes, "excluded": excluded, "data_from": data_from}


_WATCHLIST_TABLE_NAME = "watchlist"

_WATCHLIST_SCHEMA = [
    bigquery.SchemaField("ticker", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("added_at", "TIMESTAMP", mode="REQUIRED"),
    # PUL-74 made this the canonical identity; PUL-88 removed the legacy
    # client_id column it replaced. Stays NULLABLE — the live table carries
    # rows written before the column existed.
    bigquery.SchemaField("user_id", "STRING", mode="NULLABLE"),
]


def create_watchlist_table_if_not_exists() -> None:
    """Create the watchlist table in BigQuery if it does not already exist."""
    client = _get_client()
    table_id = _table_ref(client, _WATCHLIST_TABLE_NAME)
    try:
        client.get_table(table_id)
        logger.info("BQ table already exists: %s", table_id)
    except NotFound:
        table = bigquery.Table(table_id, schema=_WATCHLIST_SCHEMA)
        client.create_table(table)
        logger.info("BQ table created: %s", table_id)


def ensure_watchlist_schema_current() -> None:
    """Migrate the watchlist table — add missing columns.

    Thin binding over `ensure_schema_current()`. Safe to call on every API
    service startup. PUL-88 retired the `user_id = client_id` backfill that
    used to run here: it had converged (0 rows matched) yet still issued a DML
    statement on every Cloud Run cold start.
    """
    ensure_schema_current(_WATCHLIST_TABLE_NAME, _WATCHLIST_SCHEMA)


_USER_PORTFOLIO_POSITIONS_TABLE_NAME = "user_portfolio_positions"

_USER_PORTFOLIO_POSITIONS_SCHEMA = [
    bigquery.SchemaField("user_id",       "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("ticker",        "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("company_name",  "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("shares",        "FLOAT64",   mode="REQUIRED"),
    bigquery.SchemaField("avg_buy_price", "FLOAT64",   mode="REQUIRED"),
    bigquery.SchemaField("created_at",    "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("updated_at",    "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("portfolio_id",  "STRING",    mode="NULLABLE"),
]


def create_user_portfolio_positions_table_if_not_exists() -> None:
    """Create the user_portfolio_positions table in BigQuery if it does not already exist."""
    client = _get_client()
    table_id = _table_ref(client, _USER_PORTFOLIO_POSITIONS_TABLE_NAME)
    try:
        client.get_table(table_id)
        logger.info("BQ table already exists: %s", table_id)
    except NotFound:
        table = bigquery.Table(table_id, schema=_USER_PORTFOLIO_POSITIONS_SCHEMA)
        client.create_table(table)
        logger.info("BQ table created: %s", table_id)


def ensure_user_portfolio_positions_schema_current() -> None:
    """Migrate user_portfolio_positions — add any missing schema columns."""
    ensure_schema_current(_USER_PORTFOLIO_POSITIONS_TABLE_NAME, _USER_PORTFOLIO_POSITIONS_SCHEMA)


def upsert_user_portfolio_position(
    user_id: str,
    portfolio_id: str,
    ticker: str,
    company_name: str | None,
    shares: float,
    avg_buy_price: float,
) -> None:
    """Insert-or-update one portfolio position row keyed on (portfolio_id, ticker).

    MATCHED → update company_name, shares, avg_buy_price, updated_at.
    NOT MATCHED → full INSERT with created_at and updated_at set to now.
    Raises BigQueryError on failure.
    """
    client = _get_client()
    query = f"""
        MERGE `{_table_ref(client, _USER_PORTFOLIO_POSITIONS_TABLE_NAME)}` T
        USING (
            SELECT @user_id AS user_id, @portfolio_id AS portfolio_id,
                   @ticker AS ticker, @company_name AS company_name,
                   @shares AS shares, @avg_buy_price AS avg_buy_price
        ) S
        ON T.portfolio_id = S.portfolio_id AND T.ticker = S.ticker AND T.user_id = S.user_id
        WHEN MATCHED THEN
          UPDATE SET
            company_name  = S.company_name,
            shares        = S.shares,
            avg_buy_price = S.avg_buy_price,
            updated_at    = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN
          INSERT (user_id, portfolio_id, ticker, company_name, shares, avg_buy_price, created_at, updated_at)
          VALUES (S.user_id, S.portfolio_id, S.ticker, S.company_name, S.shares, S.avg_buy_price,
                  CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id",       "STRING",  user_id),
            bigquery.ScalarQueryParameter("portfolio_id",  "STRING",  portfolio_id),
            bigquery.ScalarQueryParameter("ticker",        "STRING",  ticker),
            bigquery.ScalarQueryParameter("company_name",  "STRING",  company_name),
            bigquery.ScalarQueryParameter("shares",        "FLOAT64", shares),
            bigquery.ScalarQueryParameter("avg_buy_price", "FLOAT64", avg_buy_price),
        ]
    )
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"upsert_user_portfolio_position failed: {exc}") from exc
    if job.errors:
        raise BigQueryError(f"upsert_user_portfolio_position failed: {job.errors}")
    logger.debug("upsert_user_portfolio_position: user_id=%s portfolio_id=%s ticker=%s", user_id, portfolio_id, ticker)


def merge_user_portfolio_positions_bulk(
    user_id: str, portfolio_id: str, positions: list[dict]
) -> int:
    """Upsert every position of one wallet in a SINGLE MERGE.

    Same semantics as `upsert_user_portfolio_position`, but the source is an
    array of STRUCTs so twenty tickers cost one query instead of twenty. That is
    a deliberate answer to Cloud Run's 60s request budget.

    There is deliberately NO `WHEN NOT MATCHED BY SOURCE` branch: it would delete
    holdings the export cannot contain — a physical dividend like S2B never
    appears in a broker export, and wiping it would be silent data loss.
    """
    if not positions:
        logger.info("merge_user_portfolio_positions_bulk: nothing to write")
        return 0

    client = _get_client()
    # NB: the array parameter must not be called `rows` — reserved word in BQ.
    query = f"""
        MERGE `{_table_ref(client, _USER_PORTFOLIO_POSITIONS_TABLE_NAME)}` T
        USING (
            SELECT
                @user_id AS user_id,
                @portfolio_id AS portfolio_id,
                item.ticker AS ticker,
                item.company_name AS company_name,
                item.shares AS shares,
                item.avg_buy_price AS avg_buy_price
            FROM UNNEST(@positions) AS item
        ) S
        ON T.user_id = S.user_id AND T.portfolio_id = S.portfolio_id AND T.ticker = S.ticker
        WHEN MATCHED THEN
          UPDATE SET
            company_name  = S.company_name,
            shares        = S.shares,
            avg_buy_price = S.avg_buy_price,
            updated_at    = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN
          INSERT (user_id, portfolio_id, ticker, company_name, shares, avg_buy_price, created_at, updated_at)
          VALUES (S.user_id, S.portfolio_id, S.ticker, S.company_name, S.shares, S.avg_buy_price,
                  CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
    """
    struct_type = bigquery.StructQueryParameterType(
        bigquery.ScalarQueryParameterType("STRING", name="ticker"),
        bigquery.ScalarQueryParameterType("STRING", name="company_name"),
        bigquery.ScalarQueryParameterType("FLOAT64", name="shares"),
        bigquery.ScalarQueryParameterType("FLOAT64", name="avg_buy_price"),
    )
    items = [
        bigquery.StructQueryParameter(
            None,
            bigquery.ScalarQueryParameter("ticker", "STRING", position["ticker"]),
            bigquery.ScalarQueryParameter("company_name", "STRING", position.get("company_name")),
            bigquery.ScalarQueryParameter("shares", "FLOAT64", float(position["shares"])),
            bigquery.ScalarQueryParameter("avg_buy_price", "FLOAT64", float(position["avg_buy_price"])),
        )
        for position in positions
    ]
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
            bigquery.ScalarQueryParameter("portfolio_id", "STRING", portfolio_id),
            bigquery.ArrayQueryParameter("positions", struct_type, items),
        ]
    )
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"merge_user_portfolio_positions_bulk failed: {exc}") from exc
    if job.errors:
        raise BigQueryError(f"merge_user_portfolio_positions_bulk failed: {job.errors}")
    written = int(job.num_dml_affected_rows or 0)
    logger.info(
        "merge_user_portfolio_positions_bulk: user_id=%s portfolio_id=%s wrote %d of %d",
        user_id, portfolio_id, written, len(positions),
    )
    return written


def delete_user_portfolio_positions(
    user_id: str, portfolio_id: str, tickers: list[str]
) -> int:
    """Delete the given tickers from one wallet in a single statement.

    An empty list issues no query at all — this is the only non-reversible path
    in the import, so an accidental unfiltered DELETE must be impossible.
    """
    if not tickers:
        logger.info("delete_user_portfolio_positions: nothing to remove")
        return 0

    client = _get_client()
    query = f"""
        DELETE FROM `{_table_ref(client, _USER_PORTFOLIO_POSITIONS_TABLE_NAME)}`
        WHERE user_id = @user_id
          AND portfolio_id = @portfolio_id
          AND ticker IN UNNEST(@tickers)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
            bigquery.ScalarQueryParameter("portfolio_id", "STRING", portfolio_id),
            bigquery.ArrayQueryParameter("tickers", "STRING", list(tickers)),
        ]
    )
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"delete_user_portfolio_positions failed: {exc}") from exc
    if job.errors:
        raise BigQueryError(f"delete_user_portfolio_positions failed: {job.errors}")
    removed = int(job.num_dml_affected_rows or 0)
    logger.info(
        "delete_user_portfolio_positions: user_id=%s portfolio_id=%s removed %d",
        user_id, portfolio_id, removed,
    )
    return removed


def delete_user_portfolio_position(user_id: str, portfolio_id: str, ticker: str) -> None:
    """Remove one portfolio position scoped to a wallet; silent no-op if not present.

    Raises BigQueryError on query failure.
    """
    client = _get_client()
    query = f"""
        DELETE FROM `{_table_ref(client, _USER_PORTFOLIO_POSITIONS_TABLE_NAME)}`
        WHERE user_id = @user_id AND portfolio_id = @portfolio_id AND ticker = @ticker
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id",      "STRING", user_id),
            bigquery.ScalarQueryParameter("portfolio_id", "STRING", portfolio_id),
            bigquery.ScalarQueryParameter("ticker",       "STRING", ticker),
        ]
    )
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"delete_user_portfolio_position failed: {exc}") from exc
    if job.errors:
        raise BigQueryError(f"delete_user_portfolio_position failed: {job.errors}")
    logger.debug("delete_user_portfolio_position: user_id=%s portfolio_id=%s ticker=%s", user_id, portfolio_id, ticker)


_PRICE_HISTORY_SESSIONS = 30  # trading sessions carried in price_history[]
_PRICE_HISTORY_SCAN_DAYS = 90  # scan floor — generous margin over 30 sessions given ~31% daily gaps


def list_user_portfolio_positions(
    user_id: str, portfolio_id: str | None = None, include_history: bool = False
) -> list[dict]:
    """Return positions for user_id joined with the latest available close price.

    When portfolio_id is provided, results are scoped to that wallet. Without it,
    all positions for the user are returned (used by the treemap endpoint for a
    single-call batch fetch, grouped by portfolio_id in Python).
    Uses ROW_NUMBER() OVER PARTITION BY ticker to pick the most recent company_daily_stats
    entry per ticker, then LEFT JOIN so positions without price data still appear.
    Rows whose close is NULL are skipped when ranking: the official GPW feed reports
    no close for an instrument that did not trade that session, and the newest row
    winning regardless would blank a held position rather than carry the previous
    session's price forward. `price_as_of` then reports the older date, which is
    what that field is for.

    When include_history=True, each row also carries price_history: list[float] — the
    last 30 trading-session close prices (PLN, ascending by date), unioned across
    company_daily_stats and etf_quotes so ETFs are covered too; None when the ticker
    has no rows. The treemap path leaves include_history=False so it never pays the
    ARRAY_AGG cost. Raises BigQueryError on query failure.
    """
    client = _get_client()
    _t = time.time()
    portfolio_filter = "AND p.portfolio_id = @portfolio_id" if portfolio_id is not None else ""
    if include_history:
        history_cte = f""",
        hist_raw AS (
          SELECT ticker, snapshot_date, kurs_zamkniecia, 0 AS src
          FROM `{_table_ref(client, _COMPANY_DAILY_STATS_TABLE_NAME)}`
          WHERE snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL {_PRICE_HISTORY_SCAN_DAYS} DAY)
            AND kurs_zamkniecia IS NOT NULL
          UNION ALL
          SELECT ticker, snapshot_date, kurs_zamkniecia, 1 AS src
          FROM `{_table_ref(client, _ETF_QUOTES_TABLE_NAME)}`
          WHERE snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL {_PRICE_HISTORY_SCAN_DAYS} DAY)
            AND kurs_zamkniecia IS NOT NULL
        ),
        hist_dedup AS (
          SELECT ticker, snapshot_date, kurs_zamkniecia
          FROM hist_raw
          QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker, snapshot_date ORDER BY src) = 1
        ),
        hist_ranked AS (
          SELECT ticker, snapshot_date, kurs_zamkniecia,
                 ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) AS rn
          FROM hist_dedup
        ),
        price_hist AS (
          SELECT ticker, ARRAY_AGG(kurs_zamkniecia ORDER BY snapshot_date ASC) AS price_history
          FROM hist_ranked
          WHERE rn <= {_PRICE_HISTORY_SESSIONS}
          GROUP BY ticker
        )"""
        history_select = ",\n          ph.price_history AS price_history"
        history_join = "LEFT JOIN price_hist ph ON p.ticker = ph.ticker"
    else:
        history_cte = ""
        history_select = ""
        history_join = ""
    query = f"""
        WITH latest_stats AS (
          SELECT
            ticker,
            kurs_zamkniecia,
            zmiana_procentowa,
            zmiana_kwotowa,
            CAST(snapshot_date AS STRING) AS price_as_of,
            ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) AS rn
          FROM `{_table_ref(client, _COMPANY_DAILY_STATS_TABLE_NAME)}`
          WHERE snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
            AND kurs_zamkniecia IS NOT NULL
        ),
        latest_etf AS (
          SELECT
            ticker,
            kurs_zamkniecia,
            zmiana_procentowa,
            zmiana_kwotowa,
            CAST(snapshot_date AS STRING) AS price_as_of,
            ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) AS rn
          FROM `{_table_ref(client, _ETF_QUOTES_TABLE_NAME)}`
          WHERE snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
            AND kurs_zamkniecia IS NOT NULL
        ){history_cte}
        SELECT
          p.portfolio_id,
          p.ticker,
          p.company_name,
          p.shares,
          p.avg_buy_price,
          IF(p.ticker = '{CASH_TICKER}', 1.0,
             COALESCE(ls.kurs_zamkniecia, etf.kurs_zamkniecia))   AS current_price,
          IF(p.ticker = '{CASH_TICKER}', 0.0,
             COALESCE(ls.zmiana_procentowa, etf.zmiana_procentowa)) AS daily_change_pct,
          -- The session's absolute move per share.  The table view used to derive
          -- this as current_price x pct, which overstates the move by exactly the
          -- day's own factor (the correct base is the *previous* close) and so
          -- disagreed with the calendar, which has always summed zmiana_kwotowa.
          IF(p.ticker = '{CASH_TICKER}', 0.0,
             COALESCE(ls.zmiana_kwotowa, etf.zmiana_kwotowa))    AS daily_change_per_share,
          IF(p.ticker = '{CASH_TICKER}', CAST(CURRENT_DATE() AS STRING),
             COALESCE(ls.price_as_of, etf.price_as_of))          AS price_as_of{history_select}
        FROM `{_table_ref(client, _USER_PORTFOLIO_POSITIONS_TABLE_NAME)}` p
        LEFT JOIN latest_stats ls
          ON p.ticker = ls.ticker AND ls.rn = 1
        LEFT JOIN latest_etf etf
          ON p.ticker = etf.ticker AND etf.rn = 1
        {history_join}
        WHERE p.user_id = @user_id {portfolio_filter}
        ORDER BY p.ticker
    """
    params: list[bigquery.ScalarQueryParameter] = [
        bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
    ]
    if portfolio_id is not None:
        params.append(bigquery.ScalarQueryParameter("portfolio_id", "STRING", portfolio_id))
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"list_user_portfolio_positions failed: {exc}") from exc
    logger.debug("BQ list_user_portfolio_positions: %.0fms", (time.time() - _t) * 1000)
    return [dict(row) for row in rows]


# Reserved ticker for uninvested cash (PUL-95 Phase 8). Kept as an ordinary
# position row so value, treemap, calendar and chart all count it without a
# parallel code path, and priced at 1.00 PLN in every query that resolves prices
# — the market-data tables have no row for it and never will. The leading
# underscore cannot collide with a GPW ticker.
CASH_TICKER = "_CASH"

_USER_PORTFOLIOS_TABLE_NAME = "user_portfolios"

_USER_PORTFOLIOS_SCHEMA = [
    bigquery.SchemaField("user_id",        "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("portfolio_id",   "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("portfolio_type", "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("portfolio_name", "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("display_order",  "INTEGER",   mode="REQUIRED"),
    bigquery.SchemaField("created_at",     "TIMESTAMP", mode="REQUIRED"),
]

_PORTFOLIO_DISPLAY_ORDER: dict[str, int] = {
    "glowny": 1, "ikze": 2, "ike": 3, "ppk": 6, "ppe": 7,
}


def create_user_portfolios_table_if_not_exists() -> None:
    """Create the user_portfolios table in BigQuery if it does not already exist."""
    client = _get_client()
    table_id = _table_ref(client, _USER_PORTFOLIOS_TABLE_NAME)
    try:
        client.get_table(table_id)
        logger.info("BQ table already exists: %s", table_id)
    except NotFound:
        table = bigquery.Table(table_id, schema=_USER_PORTFOLIOS_SCHEMA)
        client.create_table(table)
        logger.info("BQ table created: %s", table_id)


def ensure_user_portfolios_schema_current() -> None:
    """Migrate user_portfolios — add any missing schema columns."""
    ensure_schema_current(_USER_PORTFOLIOS_TABLE_NAME, _USER_PORTFOLIOS_SCHEMA)


def list_user_portfolios(user_id: str) -> list[dict]:
    """Return all wallets for user_id ordered by display_order, then created_at.

    Raises BigQueryError on query failure.
    """
    client = _get_client()
    query = f"""
        SELECT *
        FROM `{_table_ref(client, _USER_PORTFOLIOS_TABLE_NAME)}`
        WHERE user_id = @user_id
        ORDER BY display_order ASC, created_at ASC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("user_id", "STRING", user_id)]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"list_user_portfolios failed: {exc}") from exc
    return [dict(row) for row in rows]


def create_user_portfolio(
    user_id: str, portfolio_type: str, portfolio_name: str | None
) -> str:
    """Insert a new wallet and return its portfolio_id (UUID).

    Uses conditional INSERT (SELECT … WHERE …) so the uniqueness check is atomic
    at the BQ layer — prevents duplicate wallets from concurrent requests.
    Raises BigQueryError on BQ failure or constraint violation (0 rows inserted).
    """
    portfolio_id = str(uuid.uuid4())
    client = _get_client()
    table = _table_ref(client, _USER_PORTFOLIOS_TABLE_NAME)

    if portfolio_type == "inny":
        # Compute display_order (4 or 5) and enforce max-2 in the same round-trip.
        query = f"""
            INSERT INTO `{table}`
              (user_id, portfolio_id, portfolio_type, portfolio_name, display_order, created_at)
            SELECT
              @user_id, @portfolio_id, 'inny', @portfolio_name,
              CASE WHEN inny_count = 0 THEN 4 ELSE 5 END,
              CURRENT_TIMESTAMP()
            FROM (
              SELECT COUNT(*) AS inny_count
              FROM `{table}`
              WHERE user_id = @user_id AND portfolio_type = 'inny'
            )
            WHERE inny_count < 2
        """
        params: list[bigquery.ScalarQueryParameter] = [
            bigquery.ScalarQueryParameter("user_id",        "STRING", user_id),
            bigquery.ScalarQueryParameter("portfolio_id",   "STRING", portfolio_id),
            bigquery.ScalarQueryParameter("portfolio_name", "STRING", portfolio_name),
        ]
        constraint_msg = "Maximum 2 'Inny' wallets allowed"
    else:
        display_order = _PORTFOLIO_DISPLAY_ORDER.get(portfolio_type, 99)
        query = f"""
            INSERT INTO `{table}`
              (user_id, portfolio_id, portfolio_type, portfolio_name, display_order, created_at)
            SELECT @user_id, @portfolio_id, @portfolio_type, @portfolio_name, @display_order,
                   CURRENT_TIMESTAMP()
            FROM (SELECT 1)
            WHERE NOT EXISTS (
              SELECT 1 FROM `{table}`
              WHERE user_id = @user_id AND portfolio_type = @portfolio_type
            )
        """
        params = [
            bigquery.ScalarQueryParameter("user_id",        "STRING",  user_id),
            bigquery.ScalarQueryParameter("portfolio_id",   "STRING",  portfolio_id),
            bigquery.ScalarQueryParameter("portfolio_type", "STRING",  portfolio_type),
            bigquery.ScalarQueryParameter("portfolio_name", "STRING",  portfolio_name),
            bigquery.ScalarQueryParameter("display_order",  "INTEGER", display_order),
        ]
        constraint_msg = "Wallet type already exists"

    job_config = bigquery.QueryJobConfig(query_parameters=params)
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"create_user_portfolio failed: {exc}") from exc
    if job.errors:
        raise BigQueryError(f"create_user_portfolio failed: {job.errors}")
    if job.num_dml_affected_rows == 0:
        raise BigQueryError(f"create_user_portfolio: {constraint_msg}")
    logger.debug("create_user_portfolio: user_id=%s portfolio_id=%s type=%s", user_id, portfolio_id, portfolio_type)
    return portfolio_id


def delete_user_portfolio(user_id: str, portfolio_id: str) -> None:
    """Delete a wallet and cascade-delete its positions and imported operations.

    The wallet row goes last: if a cascade step fails, the wallet is still listed
    and the user can retry, whereas the reverse order would strand rows under an
    id nothing references any more.  Imported broker operations are part of that
    cascade — the dividend summary sums them per user, so leaving them behind
    keeps a deleted wallet's payouts in the "Wszystkie" totals forever.

    Raises BigQueryError on query failure.
    """
    client = _get_client()
    params = [
        bigquery.ScalarQueryParameter("user_id",      "STRING", user_id),
        bigquery.ScalarQueryParameter("portfolio_id", "STRING", portfolio_id),
    ]
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    cascade = [
        _USER_PORTFOLIO_POSITIONS_TABLE_NAME,
        _USER_BROKER_OPERATIONS_TABLE_NAME,
        _USER_PORTFOLIOS_TABLE_NAME,
    ]
    try:
        for table_name in cascade:
            query = f"""
                DELETE FROM `{_table_ref(client, table_name)}`
                WHERE user_id = @user_id AND portfolio_id = @portfolio_id
            """
            client.query(query, job_config=job_config).result()
    except Exception as exc:
        raise BigQueryError(f"delete_user_portfolio failed: {exc}") from exc
    logger.debug("delete_user_portfolio: user_id=%s portfolio_id=%s", user_id, portfolio_id)


def assign_orphan_positions_to_portfolio(user_id: str, portfolio_id: str) -> None:
    """Assign NULL-portfolio_id positions (pre-PUL-64) to the given wallet.

    Called when user creates their first Główny wallet to make existing positions visible.
    Raises BigQueryError on query failure.
    """
    client = _get_client()
    query = f"""
        UPDATE `{_table_ref(client, _USER_PORTFOLIO_POSITIONS_TABLE_NAME)}`
        SET portfolio_id = @portfolio_id
        WHERE user_id = @user_id AND portfolio_id IS NULL
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id",      "STRING", user_id),
            bigquery.ScalarQueryParameter("portfolio_id", "STRING", portfolio_id),
        ]
    )
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"assign_orphan_positions_to_portfolio failed: {exc}") from exc
    logger.debug("assign_orphan_positions_to_portfolio: user_id=%s portfolio_id=%s", user_id, portfolio_id)


_USERS_TABLE_NAME = "users"

_USERS_SCHEMA = [
    bigquery.SchemaField("user_id",       "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("email",         "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("created_at",    "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("last_login_at", "TIMESTAMP", mode="NULLABLE"),
    # PUL-83: NULL means "user" — no backfill was run when the column was added;
    # every reader must COALESCE(role, 'user').
    bigquery.SchemaField("role",          "STRING",    mode="NULLABLE"),
]


def create_users_table_if_not_exists() -> None:
    """Create the users table in BigQuery if it does not already exist."""
    client = _get_client()
    table_id = _table_ref(client, _USERS_TABLE_NAME)
    try:
        client.get_table(table_id)
        logger.info("BQ table already exists: %s", table_id)
    except NotFound:
        table = bigquery.Table(table_id, schema=_USERS_SCHEMA)
        client.create_table(table)
        logger.info("BQ table created: %s", table_id)


def ensure_users_schema_current() -> None:
    """Migrate the users table — add any missing schema columns."""
    ensure_schema_current(_USERS_TABLE_NAME, _USERS_SCHEMA)


def insert_user(user_id: str, email: str) -> None:
    """Insert one users row on registration; created_at set server-side.

    Raises BigQueryError on failure.
    """
    client = _get_client()
    query = f"""
        INSERT INTO `{_table_ref(client, _USERS_TABLE_NAME)}` (user_id, email, created_at, role)
        VALUES (@user_id, @email, CURRENT_TIMESTAMP(), 'user')
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
            bigquery.ScalarQueryParameter("email",   "STRING", email),
        ]
    )
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"insert_user failed: {exc}") from exc
    if job.errors:
        raise BigQueryError(f"insert_user failed: {job.errors}")
    logger.debug("insert_user: user_id=%s", user_id)


def upsert_user_login(user_id: str, email: str) -> None:
    """Record a login: bump last_login_at, self-healing the row if registration
    never landed it (partial-fail recovery — the register path only logs BQ errors).

    MATCHED → update last_login_at.
    NOT MATCHED → full INSERT with created_at and last_login_at set to now.
    Raises BigQueryError on failure.
    """
    client = _get_client()
    query = f"""
        MERGE `{_table_ref(client, _USERS_TABLE_NAME)}` T
        USING (
            SELECT @user_id AS user_id, @email AS email
        ) S
        ON T.user_id = S.user_id
        WHEN MATCHED THEN
          UPDATE SET last_login_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN
          INSERT (user_id, email, created_at, last_login_at, role)
          VALUES (S.user_id, S.email, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), 'user')
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
            bigquery.ScalarQueryParameter("email",   "STRING", email),
        ]
    )
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"upsert_user_login failed: {exc}") from exc
    if job.errors:
        raise BigQueryError(f"upsert_user_login failed: {job.errors}")
    logger.debug("upsert_user_login: user_id=%s", user_id)


def get_user_role(user_id: str) -> str:
    """Read a user's role — called ONLY at login (the claim then rides the JWT).

    NULL role means "user": the column was added without a backfill (PUL-83),
    so every read goes through COALESCE. A missing row also reads "user" —
    register's insert may have failed and upsert_user_login self-heals it on
    this very login. Raises BigQueryError on failure (caller decides fallback).
    """
    client = _get_client()
    query = f"""
        SELECT COALESCE(role, 'user') AS role
        FROM `{_table_ref(client, _USERS_TABLE_NAME)}`
        WHERE user_id = @user_id
        LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"get_user_role failed: {exc}") from exc
    role = rows[0].role if rows else "user"
    logger.debug("get_user_role: user_id=%s role=%s", user_id, role)
    return role


def add_watchlist_ticker(user_id: str, ticker: str) -> None:
    """Add `ticker` to `user_id`'s watchlist; silent no-op if already present.

    Raises BigQueryError if the query job fails.
    """
    client = _get_client()
    query = f"""
        INSERT INTO `{_table_ref(client, _WATCHLIST_TABLE_NAME)}` (user_id, ticker, added_at)
        SELECT @user_id, @ticker, CURRENT_TIMESTAMP()
        FROM (SELECT 1)
        WHERE NOT EXISTS (
            SELECT 1 FROM `{_table_ref(client, _WATCHLIST_TABLE_NAME)}`
            WHERE user_id = @user_id AND ticker = @ticker
        )
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
            bigquery.ScalarQueryParameter("ticker", "STRING", ticker),
        ]
    )
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"add_watchlist_ticker failed: {exc}") from exc
    if job.errors:
        raise BigQueryError(f"add_watchlist_ticker failed: {job.errors}")
    logger.debug("add_watchlist_ticker: user_id=%s ticker=%s", user_id, ticker)


def remove_watchlist_ticker(user_id: str, ticker: str) -> None:
    """Remove `ticker` from `user_id`'s watchlist; no-op if not present.

    Raises BigQueryError if the query job fails.
    """
    client = _get_client()
    query = f"""
        DELETE FROM `{_table_ref(client, _WATCHLIST_TABLE_NAME)}`
        WHERE user_id = @user_id AND ticker = @ticker
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
            bigquery.ScalarQueryParameter("ticker", "STRING", ticker),
        ]
    )
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"remove_watchlist_ticker failed: {exc}") from exc
    if job.errors:
        raise BigQueryError(f"remove_watchlist_ticker failed: {job.errors}")
    logger.debug("remove_watchlist_ticker: user_id=%s ticker=%s", user_id, ticker)


def list_watchlist_tickers(user_id: str) -> list[str]:
    """Return `user_id`'s watchlisted tickers, most recently added first.

    Raises BigQueryError if the query job fails.
    """
    client = _get_client()
    query = f"""
        SELECT ticker
        FROM `{_table_ref(client, _WATCHLIST_TABLE_NAME)}`
        WHERE user_id = @user_id
        ORDER BY added_at DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"list_watchlist_tickers failed: {exc}") from exc
    return [row.ticker for row in rows]


_COMPANIES_TABLE_NAME = "companies"

_COMPANIES_SCHEMA = [
    bigquery.SchemaField("ticker", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("hop_url", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("isin", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
]


def create_companies_table_if_not_exists() -> None:
    """Create the companies table in BigQuery if it does not already exist."""
    client = _get_client()
    table_id = _table_ref(client, _COMPANIES_TABLE_NAME)
    try:
        client.get_table(table_id)
        logger.info("BQ table already exists: %s", table_id)
    except NotFound:
        table = bigquery.Table(table_id, schema=_COMPANIES_SCHEMA)
        client.create_table(table)
        logger.info("BQ table created: %s", table_id)


def ensure_companies_schema_current() -> None:
    """Migrate the companies table — add any missing schema columns.

    Thin binding over `ensure_schema_current()`; idempotent and safe to call on
    every API/pipeline startup, matching the watchlist/x_posts migration convention.
    """
    ensure_schema_current(_COMPANIES_TABLE_NAME, _COMPANIES_SCHEMA)


def upsert_company(
    ticker: str,
    name: str | None,
    hop_url: str | None,
    isin: str | None,
) -> None:
    """Insert-or-update one companies row keyed on `ticker`.

    Last-write-wins on conflict: both write paths (parser hop, seed script) parse
    the same bankier profile page format, so neither produces a partial row worth
    protecting against overwrite. Raises BigQueryError if the MERGE fails.
    """
    client = _get_client()
    query = f"""
        MERGE `{_table_ref(client, _COMPANIES_TABLE_NAME)}` T
        USING (SELECT @ticker AS ticker, @name AS name, @hop_url AS hop_url, @isin AS isin) S
        ON T.ticker = S.ticker
        WHEN MATCHED THEN
          UPDATE SET name = S.name, hop_url = S.hop_url, isin = S.isin, updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN
          INSERT (ticker, name, hop_url, isin, created_at, updated_at)
          VALUES (S.ticker, S.name, S.hop_url, S.isin, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("ticker", "STRING", ticker),
            bigquery.ScalarQueryParameter("name", "STRING", name),
            bigquery.ScalarQueryParameter("hop_url", "STRING", hop_url),
            bigquery.ScalarQueryParameter("isin", "STRING", isin),
        ]
    )
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"upsert_company failed: {exc}") from exc
    if job.errors:
        raise BigQueryError(f"upsert_company failed: {job.errors}")
    logger.debug("upsert_company: ticker=%s", ticker)


def insert_company_if_absent(
    ticker: str,
    name: str | None,
    hop_url: str | None,
    isin: str | None,
) -> None:
    """Insert one companies row only when no row exists for that ticker.

    Never touches existing rows — safe to call with partial data (e.g. null name)
    because it will not overwrite an existing populated name/isin. Use
    upsert_company() when you have a fresh profile-page fetch and want full
    last-write-wins semantics. Raises BigQueryError if the MERGE fails.
    """
    client = _get_client()
    query = f"""
        MERGE `{_table_ref(client, _COMPANIES_TABLE_NAME)}` T
        USING (SELECT @ticker AS ticker, @name AS name, @hop_url AS hop_url, @isin AS isin) S
        ON T.ticker = S.ticker
        WHEN NOT MATCHED THEN
          INSERT (ticker, name, hop_url, isin, created_at, updated_at)
          VALUES (S.ticker, S.name, S.hop_url, S.isin, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("ticker", "STRING", ticker),
            bigquery.ScalarQueryParameter("name", "STRING", name),
            bigquery.ScalarQueryParameter("hop_url", "STRING", hop_url),
            bigquery.ScalarQueryParameter("isin", "STRING", isin),
        ]
    )
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"insert_company_if_absent failed: {exc}") from exc
    if job.errors:
        raise BigQueryError(f"insert_company_if_absent failed: {job.errors}")
    logger.debug("insert_company_if_absent: ticker=%s", ticker)


def is_processed(url: str) -> bool:
    """Return True if the announcement URL has already been inserted."""
    client = _get_client()
    ann_id = _announcement_id(url)
    query = f"SELECT COUNT(*) AS cnt FROM `{_table_ref(client)}` WHERE announcement_id = @id"
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", ann_id)]
    )
    rows = list(client.query(query, job_config=job_config).result())
    return rows[0].cnt > 0


def insert_announcement(
    url: str,
    published_at: datetime,
    title: str,
    priority: str | None = None,
) -> str:
    """Insert a new announcement row and return its announcement_id.

    Uses DML INSERT (not streaming) so subsequent UPDATE/DELETE in the same
    session are not blocked by the streaming buffer.
    Raises BigQueryError if the query job fails.
    company and ticker are not set here — the parser populates them via
    update_parsed_content() after a second HTTP hop to the company profile page.
    """
    client = _get_client()
    ann_id = _announcement_id(url)
    query = f"""
        INSERT INTO `{_table_ref(client)}`
            (announcement_id, url, published_at, title, priority)
        VALUES
            (@id, @url, @published_at, @title, @priority)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("id", "STRING", ann_id),
            bigquery.ScalarQueryParameter("url", "STRING", url),
            bigquery.ScalarQueryParameter("published_at", "TIMESTAMP", published_at),
            bigquery.ScalarQueryParameter("title", "STRING", title),
            bigquery.ScalarQueryParameter("priority", "STRING", priority),
        ]
    )
    job = client.query(query, job_config=job_config)
    job.result()
    if job.errors:
        raise BigQueryError(f"insert_announcement failed: {job.errors}")
    logger.debug("Inserted announcement_id=%s", ann_id)
    return ann_id


def update_parsed_content(
    announcement_id: str,
    parsed_content: str | None,
    ticker: str | None,
    company: str | None,
) -> None:
    """Update parsed_content, ticker, company for an existing announcement row.

    parsed_content=None is valid (parse failed gracefully).
    Raises BigQueryError if the UPDATE fails or matches 0 rows.
    """
    client = _get_client()
    query = f"""
        UPDATE `{_table_ref(client)}`
        SET
            parsed_content = @parsed_content,
            ticker = @ticker,
            company = @company
        WHERE announcement_id = @id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("parsed_content", "STRING", parsed_content),
            bigquery.ScalarQueryParameter("ticker", "STRING", ticker),
            bigquery.ScalarQueryParameter("company", "STRING", company),
            bigquery.ScalarQueryParameter("id", "STRING", announcement_id),
        ]
    )
    job = client.query(query, job_config=job_config)
    job.result()
    if job.errors:
        raise BigQueryError(f"update_parsed_content failed: {job.errors}")
    if job.num_dml_affected_rows == 0:
        raise BigQueryError(
            f"update_parsed_content: no row matched announcement_id={announcement_id!r}"
        )
    logger.debug("Updated parsed_content for announcement_id=%s", announcement_id)


def save_analysis_result(
    announcement_id: str,
    structured_analysis: str | None,
    analysis_approved: bool | None,
    analysis_reject_reason: str | None,
    event_type: str | None,
    analysis_score: float | None,
) -> None:
    """Update an announcement row with S-03 analysis results.

    Raises BigQueryError if the UPDATE fails or matches 0 rows.
    """
    client = _get_client()
    query = f"""
        UPDATE `{_table_ref(client)}`
        SET
            structured_analysis = @structured_analysis,
            analysis_approved = @analysis_approved,
            analysis_reject_reason = @analysis_reject_reason,
            event_type = @event_type,
            analysis_score = @analysis_score,
            analyzed_at = CURRENT_TIMESTAMP()
        WHERE announcement_id = @id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("structured_analysis", "STRING", structured_analysis),
            bigquery.ScalarQueryParameter("analysis_approved", "BOOL", analysis_approved),
            bigquery.ScalarQueryParameter("analysis_reject_reason", "STRING", analysis_reject_reason),
            bigquery.ScalarQueryParameter("event_type", "STRING", event_type),
            bigquery.ScalarQueryParameter("analysis_score", "FLOAT64", analysis_score),
            bigquery.ScalarQueryParameter("id", "STRING", announcement_id),
        ]
    )
    job = client.query(query, job_config=job_config)
    job.result()
    if job.errors:
        raise BigQueryError(f"save_analysis_result failed: {job.errors}")
    if job.num_dml_affected_rows == 0:
        raise BigQueryError(
            f"save_analysis_result: no row matched announcement_id={announcement_id!r}"
        )
    logger.debug("Saved analysis result for announcement_id=%s", announcement_id)


def fetch_top_n_for_window(
    window_start: datetime,
    window_end: datetime,
    n: int = 4,
    min_score: float = 50,  # mirrors post_main.MIN_XPOST_SCORE (the tunable source of truth)
) -> list[dict]:
    """Return up to N approved announcements for a time window, one per company.

    Only announcements with `analysis_score >= min_score` qualify (PUL-27 quality
    gate). Filtering at fetch time gates the WHOLE pipeline (generation + email +
    publish): an empty pool after filtering routes to the existing no-post path,
    never an empty thread. The caller passes MIN_XPOST_SCORE.

    Also excludes 'inne'-categorized announcements — they are not eligible for X posts.

    Selection (PUL-40): the SQL over-fetches all qualifying rows in the window,
    deterministically ordered by `analysis_score DESC, published_at DESC` and
    bounded by a generous safety cap; `select_top_companies` then does
    dedup-before-limit (one row per distinct ticker, first occurrence wins) and
    drops number-less `wyniki_*` rows *before* the top-N cut so a freed slot
    backfills. This makes N = N distinct companies, not N raw rows.

    Returns list of dicts with keys: announcement_id, ticker, company, title,
    structured_analysis, event_type, analysis_score, url — at most N, score DESC.
    Empty list if none found. Raises BigQueryError on query failure.
    """
    client = _get_client()
    query = f"""
        SELECT
            announcement_id, ticker, company, title,
            structured_analysis, event_type, analysis_score, url
        FROM `{_table_ref(client)}`
        WHERE analysis_approved = TRUE
          AND event_type != 'inne'
          AND published_at BETWEEN @window_start AND @window_end
          AND analysis_score >= @min_score
        ORDER BY analysis_score DESC, published_at DESC
        LIMIT {_FETCH_SAFETY_CAP}
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("window_start", "TIMESTAMP", window_start),
            bigquery.ScalarQueryParameter("window_end", "TIMESTAMP", window_end),
            bigquery.ScalarQueryParameter("min_score", "FLOAT64", min_score),
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"fetch_top_n_for_window failed: {exc}") from exc
    candidates = [
        {
            "announcement_id": row.announcement_id,
            "ticker": row.ticker,
            "company": row.company,
            "title": row.title,
            "structured_analysis": row.structured_analysis,
            "event_type": row.event_type,
            "analysis_score": row.analysis_score,
            "url": row.url,
        }
        for row in rows
    ]
    return select_top_companies(candidates, n)


def _build_filter_clauses(
    approved_only: bool = False,
    ticker: str | None = None,
    company: str | None = None,
    event_type: str | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
) -> tuple[str, list[bigquery.ScalarQueryParameter]]:
    clauses, params = [], []
    if approved_only:
        clauses.append("analysis_approved = TRUE")
    if ticker:
        clauses.append("ticker = @ticker")
        params.append(bigquery.ScalarQueryParameter("ticker", "STRING", ticker))
    if company:
        clauses.append("LOWER(company) LIKE LOWER(@company)")
        params.append(bigquery.ScalarQueryParameter("company", "STRING", f"%{company}%"))
    if event_type:
        clauses.append("event_type = @event_type")
        params.append(bigquery.ScalarQueryParameter("event_type", "STRING", event_type))
    if from_dt:
        clauses.append("published_at >= @from_dt")
        params.append(bigquery.ScalarQueryParameter("from_dt", "TIMESTAMP", from_dt))
    else:
        clauses.append(
            f"published_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {_ANNOUNCEMENTS_DEFAULT_DAYS} DAY)"
        )
    if to_dt:
        clauses.append("published_at <= @to_dt")
        params.append(bigquery.ScalarQueryParameter("to_dt", "TIMESTAMP", to_dt))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def list_announcements_admin(
    page: int = 1,
    page_size: int = 20,
    ticker: str | None = None,
    company: str | None = None,
    event_type: str | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
) -> list[dict]:
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    client = _get_client()
    _t = time.time()
    offset = (page - 1) * page_size
    where, filter_params = _build_filter_clauses(
        approved_only=False,
        ticker=ticker,
        company=company,
        event_type=event_type,
        from_dt=from_dt if from_dt is not None else datetime.min,
        to_dt=to_dt,
    )
    # LEFT JOIN x_posts so posts written after PUL-29 (post_text lives in x_posts, not
    # announcements) still surface; COALESCE falls back to the deprecated announcements
    # columns for rows posted before the cutover. Filter columns from _build_filter_clauses
    # are announcements-only and have no x_posts namesake, so they stay unambiguous.
    query = f"""
        SELECT
            a.announcement_id, a.url, a.published_at, a.title, a.company, a.ticker,
            COALESCE(x.post_text, a.post_text) AS post_text,
            COALESCE(x.posted_at, a.posted_at) AS posted_at,
            a.analyzed_at,
            COALESCE(x.supervisor_attempts, a.supervisor_attempts) AS supervisor_attempts,
            a.parsed_content, a.priority, a.structured_analysis, a.analysis_approved,
            a.analysis_reject_reason, a.event_type, a.analysis_score, a.x_post_id
        FROM `{_table_ref(client)}` AS a
        LEFT JOIN `{_table_ref(client, _X_POSTS_TABLE_NAME)}` AS x
            ON a.x_post_id = x.x_post_id
        {where}
        ORDER BY a.published_at DESC
        LIMIT @page_size OFFSET @offset
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("page_size", "INT64", page_size),
            bigquery.ScalarQueryParameter("offset", "INT64", offset),
            *filter_params,
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"list_announcements_admin failed: {exc}") from exc
    logger.debug("BQ list_announcements_admin: %.0fms", (time.time() - _t) * 1000)
    return [
        {
            "announcement_id": row.announcement_id,
            "url": row.url,
            "published_at": row.published_at,
            "title": row.title,
            "company": row.company,
            "ticker": row.ticker,
            "post_text": row.post_text,
            "posted_at": row.posted_at,
            "analyzed_at": row.analyzed_at,
            "supervisor_attempts": row.supervisor_attempts,
            "x_post_id": row.x_post_id,
            "parsed_content": row.parsed_content,
            "priority": row.priority,
            "structured_analysis": row.structured_analysis,
            "analysis_approved": row.analysis_approved,
            "analysis_reject_reason": row.analysis_reject_reason,
            "event_type": row.event_type,
            "analysis_score": row.analysis_score,
        }
        for row in rows
    ]


def _build_x_posts_filter_clauses(
    window: str | None = None,
    x_publish_status: str | None = None,
    post_text: str | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
) -> tuple[str, list[bigquery.ScalarQueryParameter]]:
    clauses, params = [], []
    if window:
        clauses.append("`window` = @window")
        params.append(bigquery.ScalarQueryParameter("window", "STRING", window))
    if x_publish_status:
        clauses.append("x_publish_status = @x_publish_status")
        params.append(
            bigquery.ScalarQueryParameter("x_publish_status", "STRING", x_publish_status)
        )
    if post_text:
        clauses.append("LOWER(post_text) LIKE LOWER(@post_text)")
        params.append(bigquery.ScalarQueryParameter("post_text", "STRING", f"%{post_text}%"))
    if from_dt:
        clauses.append("posted_at >= @from_dt")
        params.append(bigquery.ScalarQueryParameter("from_dt", "TIMESTAMP", from_dt))
    if to_dt:
        clauses.append("posted_at <= @to_dt")
        params.append(bigquery.ScalarQueryParameter("to_dt", "TIMESTAMP", to_dt))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def list_x_posts_admin(
    page: int = 1,
    page_size: int = 20,
    window: str | None = None,
    x_publish_status: str | None = None,
    post_text: str | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
) -> list[dict]:
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    client = _get_client()
    offset = (page - 1) * page_size
    where, filter_params = _build_x_posts_filter_clauses(
        window=window,
        x_publish_status=x_publish_status,
        post_text=post_text,
        from_dt=from_dt,
        to_dt=to_dt,
    )
    query = f"""
        SELECT
            x_post_id, `window`, post_text, tweet_ids, posted_at,
            supervisor_attempts, x_publish_status
        FROM `{_table_ref(client, _X_POSTS_TABLE_NAME)}`
        {where}
        ORDER BY posted_at DESC
        LIMIT @page_size OFFSET @offset
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("page_size", "INT64", page_size),
            bigquery.ScalarQueryParameter("offset", "INT64", offset),
            *filter_params,
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"list_x_posts_admin failed: {exc}") from exc
    return [
        {
            "x_post_id": row.x_post_id,
            "window": row.window,
            "post_text": row.post_text,
            "tweet_ids": row.tweet_ids,
            "posted_at": row.posted_at,
            "supervisor_attempts": row.supervisor_attempts,
            "x_publish_status": row.x_publish_status,
        }
        for row in rows
    ]


def list_announcements_user(
    page: int = 1,
    page_size: int = 20,
    ticker: str | None = None,
    company: str | None = None,
    event_type: str | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
) -> list[dict]:
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    client = _get_client()
    _t = time.time()
    offset = (page - 1) * page_size
    where, filter_params = _build_filter_clauses(
        approved_only=True,
        ticker=ticker,
        company=company,
        event_type=event_type,
        from_dt=from_dt,
        to_dt=to_dt,
    )
    query = f"""
        SELECT
            company, ticker, event_type, structured_analysis,
            published_at
        FROM `{_table_ref(client)}`
        {where}
        ORDER BY published_at DESC
        LIMIT @page_size OFFSET @offset
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("page_size", "INT64", page_size),
            bigquery.ScalarQueryParameter("offset", "INT64", offset),
            *filter_params,
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"list_announcements_user failed: {exc}") from exc
    logger.debug("BQ list_announcements_user: %.0fms", (time.time() - _t) * 1000)
    return [
        {
            "company": row.company,
            "ticker": row.ticker,
            "event_type": row.event_type,
            "structured_analysis": row.structured_analysis,
            "published_at": row.published_at,
        }
        for row in rows
    ]


def list_announcements_for_watchlist(
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
) -> list[dict]:
    """Return approved announcements for tickers in `user_id`'s watchlist.

    Column set of `list_announcements_user` plus `analysis_score` — the API
    layer decides per role whether the score is exposed. The watchlist
    subquery is bounded to 200 tickers per client — a defensive guardrail,
    not a user-facing limit. Raises BigQueryError on query failure.
    """
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    client = _get_client()
    _t = time.time()
    offset = (page - 1) * page_size
    where, filter_params = _build_filter_clauses(
        approved_only=True,
        from_dt=from_dt,
        to_dt=to_dt,
    )
    query = f"""
        SELECT
            a.company, a.ticker, a.event_type, a.structured_analysis,
            a.published_at, a.analysis_score
        FROM `{_table_ref(client)}` AS a
        INNER JOIN (
            SELECT ticker FROM `{_table_ref(client, _WATCHLIST_TABLE_NAME)}`
            WHERE user_id = @user_id LIMIT 200
        ) AS w ON a.ticker = w.ticker
        {where}
        ORDER BY a.published_at DESC
        LIMIT @page_size OFFSET @offset
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
            bigquery.ScalarQueryParameter("page_size", "INT64", page_size),
            bigquery.ScalarQueryParameter("offset", "INT64", offset),
            *filter_params,
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"list_announcements_for_watchlist failed: {exc}") from exc
    logger.debug("BQ list_announcements_for_watchlist: %.0fms", (time.time() - _t) * 1000)
    return [
        {
            "company": row.company,
            "ticker": row.ticker,
            "event_type": row.event_type,
            "structured_analysis": row.structured_analysis,
            "published_at": row.published_at,
            "analysis_score": row.analysis_score,
        }
        for row in rows
    ]


# ── watchlist sentiment (PUL-87) ──────────────────────────────────────────────
# Fixed 7-day window for the my-wallet sentiment bar + drill-down. Interpolated
# into the SQL as a constant (never a bound param — BQ rejects a parameter in the
# INTERVAL slot; matches the pattern at `_ANNOUNCEMENTS_DEFAULT_DAYS` usages).
_WL_SENTIMENT_WINDOW_DAYS = 7

# Shared sentiment normalization. Sentiment lives inside the structured_analysis
# JSON string, with data drift: English labels (neutral/positive/negative) and NULLs
# coexist with the Polish values. Fold everything to the three Polish buckets —
# positive→pozytywny, negative→negatywny, else neutralny (the analyzer's own
# default), so no approved announcement escapes a bucket. Reused verbatim by the
# drill-down (list_watchlist_by_sentiment) so bar counts and popup contents can't
# diverge. JSON_VALUE on the STRING column is lax (malformed JSON → NULL →
# neutralny); no SAFE. prefix (unsupported for JSON_VALUE).
_SENTIMENT_BUCKET_SQL = (
    "CASE LOWER(IFNULL(JSON_VALUE(a.structured_analysis, '$.sentiment'), '')) "
    "WHEN 'pozytywny' THEN 'pozytywny' "
    "WHEN 'positive' THEN 'pozytywny' "
    "WHEN 'negatywny' THEN 'negatywny' "
    "WHEN 'negative' THEN 'negatywny' "
    "ELSE 'neutralny' END"
)


def summarize_watchlist_sentiment(
    user_id: str, days: int = _WL_SENTIMENT_WINDOW_DAYS
) -> dict:
    """Aggregate approved watchlist announcements over the last `days` into the three
    normalized sentiment buckets, plus average score and the count of distinct days
    that actually have data (PUL-87).

    Sentiment/score are admin-only — the API layer gates this behind admin. Returns
    a dict: counts (per bucket), avg_score (rounded int or None), days_with_data,
    window_from/window_to (ISO, server UTC), total. Raises BigQueryError on failure.
    """
    client = _get_client()
    _t = time.time()
    query = f"""
        WITH scoped AS (
            SELECT
                {_SENTIMENT_BUCKET_SQL} AS bucket,
                a.analysis_score AS analysis_score,
                a.published_at AS published_at
            FROM `{_table_ref(client)}` AS a
            INNER JOIN (
                SELECT ticker FROM `{_table_ref(client, _WATCHLIST_TABLE_NAME)}`
                WHERE user_id = @user_id LIMIT 200
            ) AS w ON a.ticker = w.ticker
            WHERE a.analysis_approved = TRUE
              AND a.published_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
        )
        SELECT
            COUNTIF(bucket = 'pozytywny') AS pozytywny,
            COUNTIF(bucket = 'neutralny') AS neutralny,
            COUNTIF(bucket = 'negatywny') AS negatywny,
            COUNT(*) AS total,
            AVG(analysis_score) AS avg_score,
            COUNT(DISTINCT DATE(published_at)) AS days_with_data
        FROM scoped
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("user_id", "STRING", user_id)]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"summarize_watchlist_sentiment failed: {exc}") from exc
    logger.debug("BQ summarize_watchlist_sentiment: %.0fms", (time.time() - _t) * 1000)

    now = datetime.now(timezone.utc)
    row = rows[0] if rows else None
    avg = row.avg_score if row else None
    return {
        "counts": {
            "pozytywny": row.pozytywny if row else 0,
            "neutralny": row.neutralny if row else 0,
            "negatywny": row.negatywny if row else 0,
        },
        "avg_score": round(avg) if avg is not None else None,
        "days_with_data": row.days_with_data if row else 0,
        "window_from": (now - timedelta(days=days)).isoformat(),
        "window_to": now.isoformat(),
        "total": row.total if row else 0,
    }


def list_watchlist_by_sentiment(
    user_id: str,
    bucket: str,
    days: int = _WL_SENTIMENT_WINDOW_DAYS,
    limit: int = _FETCH_SAFETY_CAP,
) -> list[dict]:
    """List approved watchlist announcements whose normalized sentiment equals
    `bucket`, newest first, bounded (PUL-87 drill-down).

    Embeds `_SENTIMENT_BUCKET_SQL` verbatim — the SAME constant the bar summary
    uses — so the popup contents can never diverge from the bar counts. Same
    watchlist INNER-JOIN + 7-day window + approved-only slice as
    `summarize_watchlist_sentiment`; the day count is the interpolated constant
    (BQ rejects a param in the INTERVAL slot), while bucket/user_id/limit are
    bound. Column set mirrors `list_announcements_for_watchlist`. Sentiment/score
    are admin-only — the API layer gates this behind admin. Raises BigQueryError
    on query failure.
    """
    client = _get_client()
    _t = time.time()
    query = f"""
        SELECT
            a.company, a.ticker, a.event_type, a.structured_analysis,
            a.published_at, a.analysis_score
        FROM `{_table_ref(client)}` AS a
        INNER JOIN (
            SELECT ticker FROM `{_table_ref(client, _WATCHLIST_TABLE_NAME)}`
            WHERE user_id = @user_id LIMIT 200
        ) AS w ON a.ticker = w.ticker
        WHERE a.analysis_approved = TRUE
          AND a.published_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
          AND {_SENTIMENT_BUCKET_SQL} = @bucket
        ORDER BY a.published_at DESC
        LIMIT @limit
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
            bigquery.ScalarQueryParameter("bucket", "STRING", bucket),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"list_watchlist_by_sentiment failed: {exc}") from exc
    logger.debug("BQ list_watchlist_by_sentiment: %.0fms", (time.time() - _t) * 1000)
    return [
        {
            "company": row.company,
            "ticker": row.ticker,
            "event_type": row.event_type,
            "structured_analysis": row.structured_analysis,
            "published_at": row.published_at,
            "analysis_score": row.analysis_score,
        }
        for row in rows
    ]


def list_top_announcements_public(limit: int = 3) -> list[dict]:
    """Return the highest-score approved announcements for the public landing cards.

    Score containment (PUL-72): `analysis_score` is deliberately NOT in the
    SELECT list — it orders the result server-side but never leaves the DB
    layer, so the admin-only score convention holds for public callers.
    Bounded to the last 90 days (see _ANNOUNCEMENTS_DEFAULT_DAYS) so cards
    stay fresh; excludes 'inne' (same eligibility rule as X posts). Returns dicts
    with keys: company, ticker, title, event_type, published_at,
    structured_analysis. Raises BigQueryError on query failure.
    """
    client = _get_client()
    _t = time.time()
    query = f"""
        SELECT
            company, ticker, title, event_type, published_at, structured_analysis
        FROM `{_table_ref(client)}`
        WHERE analysis_approved = TRUE
          AND analysis_score IS NOT NULL
          AND event_type != 'inne'
          AND published_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {_ANNOUNCEMENTS_DEFAULT_DAYS} DAY)
        ORDER BY analysis_score DESC, published_at DESC
        LIMIT @limit
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("limit", "INT64", limit)]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"list_top_announcements_public failed: {exc}") from exc
    logger.debug("BQ list_top_announcements_public: %.0fms", (time.time() - _t) * 1000)
    return [
        {
            "company": row.company,
            "ticker": row.ticker,
            "title": row.title,
            "event_type": row.event_type,
            "published_at": row.published_at,
            "structured_analysis": row.structured_analysis,
        }
        for row in rows
    ]


def save_x_post(
    announcement_ids: list[str],
    post_text: str | None,
    window: str,
    supervisor_attempts: int,
) -> str:
    """Insert one x_posts row and link it onto the contributing announcements.

    Generates the x_post_id (UUID), INSERTs a single x_posts row (posted_at stamped
    server-side), then stamps x_post_id onto every contributing announcement row.
    post_text=None records a failed generation attempt (BQ stores NULL).

    Not atomic by design: the INSERT runs first; if the UPDATE fails or matches 0 rows
    a BigQueryError is raised and the x_posts row remains as a harmless orphan
    (posted_at still records that the post was attempted). Returns the new x_post_id.
    """
    client = _get_client()
    x_post_id = uuid.uuid4().hex

    insert_query = f"""
        INSERT INTO `{_table_ref(client, _X_POSTS_TABLE_NAME)}`
            (x_post_id, `window`, post_text, supervisor_attempts, posted_at)
        VALUES
            (@x_post_id, @window, @post_text, @supervisor_attempts, CURRENT_TIMESTAMP())
    """
    insert_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("x_post_id", "STRING", x_post_id),
            bigquery.ScalarQueryParameter("window", "STRING", window),
            bigquery.ScalarQueryParameter("post_text", "STRING", post_text),
            bigquery.ScalarQueryParameter("supervisor_attempts", "INTEGER", supervisor_attempts),
        ]
    )
    try:
        insert_job = client.query(insert_query, job_config=insert_config)
        insert_job.result()
    except Exception as exc:
        raise BigQueryError(f"save_x_post insert failed: {exc}") from exc
    if insert_job.errors:
        raise BigQueryError(f"save_x_post insert failed: {insert_job.errors}")

    update_query = f"""
        UPDATE `{_table_ref(client)}`
        SET x_post_id = @x_post_id
        WHERE announcement_id IN UNNEST(@ids)
    """
    update_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("x_post_id", "STRING", x_post_id),
            bigquery.ArrayQueryParameter("ids", "STRING", announcement_ids),
        ]
    )
    try:
        update_job = client.query(update_query, job_config=update_config)
        update_job.result()
    except Exception as exc:
        raise BigQueryError(f"save_x_post update failed: {exc}") from exc
    if update_job.errors:
        raise BigQueryError(f"save_x_post update failed: {update_job.errors}")
    if update_job.num_dml_affected_rows == 0:
        raise BigQueryError(f"save_x_post: 0 announcements updated for ids={announcement_ids!r}")
    logger.debug(
        "save_x_post: x_post_id=%s linked to %d announcements, attempts=%d",
        x_post_id, len(announcement_ids), supervisor_attempts,
    )
    return x_post_id


def update_x_post_publish_result(
    x_post_id: str,
    tweet_ids: list[str] | None,
    status: str,
) -> None:
    """Write the publish outcome onto an existing x_posts row, keyed by x_post_id.

    `tweet_ids` (if non-empty) are joined comma-separated into the STRING `tweet_ids`
    column; None/empty stores NULL. `status` is one of: published | skipped | failed |
    partial. Keeps the save_x_post INSERT path untouched — this is the publish write.
    Raises BigQueryError on failure or if no row matched the x_post_id.
    """
    client = _get_client()
    joined = ",".join(tweet_ids) if tweet_ids else None
    # No reserved-keyword columns in the SET/WHERE here (x_post_id, tweet_ids,
    # x_publish_status are all safe); kept parameterized regardless.
    query = f"""
        UPDATE `{_table_ref(client, _X_POSTS_TABLE_NAME)}`
        SET tweet_ids = @tweet_ids, x_publish_status = @status
        WHERE x_post_id = @x_post_id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("tweet_ids", "STRING", joined),
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("x_post_id", "STRING", x_post_id),
        ]
    )
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"update_x_post_publish_result failed: {exc}") from exc
    if job.errors:
        raise BigQueryError(f"update_x_post_publish_result failed: {job.errors}")
    if job.num_dml_affected_rows == 0:
        raise BigQueryError(
            f"update_x_post_publish_result: no x_posts row for x_post_id={x_post_id!r}"
        )
    logger.debug(
        "update_x_post_publish_result: x_post_id=%s status=%s tweet_ids=%s",
        x_post_id, status, joined,
    )


def x_post_already_published(window: str, day: date | None = None) -> bool:
    """True if a thread for `window` was already published on `day` (Warsaw calendar day).

    The dedup key is `DATE(posted_at)` in Europe/Warsaw — NOT the announcement-fetch
    window bounds (those cross midnight for `ranek` and bound fetch time, not publish
    time; all three windows publish on their run day). `day` defaults to today (Warsaw).
    Used before publishing to prevent double-posting on job re-run/retry.

    Accepted risk: this is a check-then-act guard, not a lock — two concurrent
    invocations for the same window could both pass before either writes. Acceptable
    given one Cloud Scheduler trigger per window. Raises BigQueryError on query failure.
    """
    client = _get_client()
    if day is None:
        day = datetime.now(ZoneInfo("Europe/Warsaw")).date()
    query = f"""
        SELECT COUNT(*) AS cnt
        FROM `{_table_ref(client, _X_POSTS_TABLE_NAME)}`
        WHERE `window` = @window
          AND x_publish_status = 'published'
          AND DATE(posted_at, 'Europe/Warsaw') = @day
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("window", "STRING", window),
            bigquery.ScalarQueryParameter("day", "DATE", day),
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"x_post_already_published failed: {exc}") from exc
    return rows[0].cnt > 0


def list_distinct_tickers() -> list[str]:
    """Return sorted list of company tickers (announcements/watchlist autocomplete)."""
    client = _get_client()
    query = f"""
        SELECT ticker FROM `{_table_ref(client, _COMPANIES_TABLE_NAME)}`
        ORDER BY ticker
    """
    try:
        rows = list(client.query(query).result())
    except Exception as exc:
        raise BigQueryError(f"list_distinct_tickers failed: {exc}") from exc
    return [row.ticker for row in rows]


def list_distinct_portfolio_tickers() -> list[str]:
    """Return sorted list of company + ETF tickers for portfolio ticker validation."""
    client = _get_client()
    query = f"""
        SELECT ticker FROM `{_table_ref(client, _COMPANIES_TABLE_NAME)}`
        UNION DISTINCT
        SELECT ticker FROM `{_table_ref(client, _ETF_INSTRUMENTS_TABLE_NAME)}`
        ORDER BY ticker
    """
    try:
        rows = list(client.query(query).result())
    except Exception as exc:
        raise BigQueryError(f"list_distinct_portfolio_tickers failed: {exc}") from exc
    return [row.ticker for row in rows]


def list_distinct_companies() -> list[str]:
    """Return sorted list of all non-null company names in the companies dimension table."""
    client = _get_client()
    query = f"""
        SELECT name
        FROM `{_table_ref(client, _COMPANIES_TABLE_NAME)}`
        WHERE name IS NOT NULL
        ORDER BY name
    """
    try:
        rows = list(client.query(query).result())
    except Exception as exc:
        raise BigQueryError(f"list_distinct_companies failed: {exc}") from exc
    return [row.name for row in rows]


def list_tickers_missing_from_companies() -> list[tuple[str, str | None]]:
    """Return (ticker, fallback_name) for every announcements ticker absent from companies.

    fallback_name is the most recent non-null `company` value for that ticker in
    announcements, for use as a backfill fallback when the bankier.pl hop fails.
    Raises BigQueryError if the query job fails.
    """
    client = _get_client()
    query = f"""
        SELECT a.ticker AS ticker,
               ARRAY_AGG(a.company IGNORE NULLS ORDER BY a.published_at DESC LIMIT 1)[SAFE_OFFSET(0)] AS fallback_name
        FROM `{_table_ref(client)}` a
        WHERE a.ticker IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM `{_table_ref(client, _COMPANIES_TABLE_NAME)}` c WHERE c.ticker = a.ticker)
        GROUP BY a.ticker
        ORDER BY a.ticker
    """
    try:
        rows = list(client.query(query).result())
    except Exception as exc:
        raise BigQueryError(f"list_tickers_missing_from_companies failed: {exc}") from exc
    return [(row.ticker, row.fallback_name) for row in rows]


def delete_announcement(announcement_id: str) -> None:
    """Delete a single announcement row by its ID.

    Raises BigQueryError if the DELETE fails or no row was matched.
    """
    client = _get_client()
    query = f"DELETE FROM `{_table_ref(client)}` WHERE announcement_id = @id"
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", announcement_id)]
    )
    job = client.query(query, job_config=job_config)
    job.result()
    if job.errors:
        raise BigQueryError(f"delete_announcement failed: {job.errors}")
    if job.num_dml_affected_rows == 0:
        raise BigQueryError(f"delete_announcement: no row matched announcement_id={announcement_id!r}")
    logger.debug("Deleted announcement_id=%s", announcement_id)


def get_processed_ids_since(cutoff: datetime) -> set[str]:
    """Return set of announcement_ids where published_at >= cutoff.

    Caller should pass cutoff = now - 2× scrape_window for a safety margin.
    Raises BigQueryError if the BQ query fails.
    """
    client = _get_client()
    query = f"SELECT announcement_id FROM `{_table_ref(client)}` WHERE published_at >= @cutoff"
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("cutoff", "TIMESTAMP", cutoff)]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"get_processed_ids_since failed: {exc}") from exc
    return {row.announcement_id for row in rows}


_COMPANY_DAILY_STATS_TABLE_NAME = "company_daily_stats"

# Any field added after initial table creation must be NULLABLE — ensure_schema_current()'s
# additive ALTER TABLE ADD COLUMN path only succeeds for NULLABLE columns in BigQuery.
_COMPANY_DAILY_STATS_SCHEMA = [
    bigquery.SchemaField("ticker", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("snapshot_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("kurs_zamkniecia", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("zmiana_procentowa", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("zmiana_kwotowa", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("kurs_otwarcia", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("kurs_min", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("kurs_max", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("wartosc_obrotu", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("liczba_transakcji", "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("fetched_at", "TIMESTAMP", mode="REQUIRED"),
    # PUL-98 — provenance and the feed's reference price. Appended last so the repo
    # literal mirrors the live table's column order after the additive migration.
    bigquery.SchemaField("source", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("kurs_odn", "FLOAT64", mode="NULLABLE"),
]


def create_company_daily_stats_table_if_not_exists() -> None:
    """Create the company_daily_stats table in BigQuery if it does not already exist.

    Partitioned by snapshot_date (DAY), clustered by ticker.
    """
    client = _get_client()
    table_id = _table_ref(client, _COMPANY_DAILY_STATS_TABLE_NAME)
    try:
        client.get_table(table_id)
        logger.info("BQ table already exists: %s", table_id)
    except NotFound:
        table = bigquery.Table(table_id, schema=_COMPANY_DAILY_STATS_SCHEMA)
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="snapshot_date",
        )
        table.clustering_fields = ["ticker"]
        client.create_table(table)
        logger.info("BQ table created: %s", table_id)


def ensure_company_daily_stats_schema_current() -> None:
    """Migrate the company_daily_stats table — add any missing schema columns.

    Thin binding over `ensure_schema_current()`; idempotent and safe to call on
    every company-stats job startup.
    """
    ensure_schema_current(_COMPANY_DAILY_STATS_TABLE_NAME, _COMPANY_DAILY_STATS_SCHEMA)


def list_companies_with_hop_info() -> list[dict]:
    """Return all companies rows as dicts with ticker, name, hop_url, isin.

    No WHERE filter — the missing-hop_url skip+log decision happens in the caller's loop.
    Raises BigQueryError if the query job fails.
    """
    client = _get_client()
    query = f"""
        SELECT ticker, name, hop_url, isin
        FROM `{_table_ref(client, _COMPANIES_TABLE_NAME)}`
        ORDER BY ticker
    """
    try:
        rows = list(client.query(query).result())
    except Exception as exc:
        raise BigQueryError(f"list_companies_with_hop_info failed: {exc}") from exc
    return [
        {"ticker": row.ticker, "name": row.name, "hop_url": row.hop_url, "isin": row.isin}
        for row in rows
    ]


def delete_company_daily_stats_for_date(snapshot_date: date) -> None:
    """Delete all company_daily_stats rows for snapshot_date.

    Called at job start so a re-run for the same day is always a clean replace.
    Raises BigQueryError on query failure.
    """
    client = _get_client()
    table = _table_ref(client, _COMPANY_DAILY_STATS_TABLE_NAME)
    query = f"DELETE FROM `{table}` WHERE snapshot_date = @snapshot_date"
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("snapshot_date", "DATE", snapshot_date)]
    )
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"delete_company_daily_stats_for_date failed: {exc}") from exc
    if job.errors:
        raise BigQueryError(f"delete_company_daily_stats_for_date failed: {job.errors}")
    logger.info("delete_company_daily_stats_for_date: deleted rows for %s", snapshot_date)


def batch_insert_company_daily_stats(rows: list[dict]) -> None:
    """Batch-insert company_daily_stats rows via BQ streaming insert (insert_rows_json).

    Each row dict must contain ticker, snapshot_date (YYYY-MM-DD string), fetched_at
    (ISO timestamp string), and the trading fields. One API call for all rows —
    orders of magnitude faster than per-row DML queries.
    Raises BigQueryError if BQ reports any row errors.
    """
    if not rows:
        logger.info("batch_insert_company_daily_stats: no rows to insert")
        return
    client = _get_client()
    table_id = _table_ref(client, _COMPANY_DAILY_STATS_TABLE_NAME)
    errors = client.insert_rows_json(table_id, rows)
    if errors:
        raise BigQueryError(f"batch_insert_company_daily_stats failed: {errors}")
    logger.info("batch_insert_company_daily_stats: inserted %d rows", len(rows))


def merge_company_daily_stats(rows: list[dict]) -> None:
    """Atomically upsert company_daily_stats rows via BigQuery MERGE.

    Uses a temp table as the MERGE source so the target table always has data —
    no deletion window between a DELETE and re-INSERT on hourly re-runs.
    Raises BigQueryError on load job or MERGE job failure.
    """
    if not rows:
        logger.info("merge_company_daily_stats: no rows to merge")
        return

    client = _get_client()
    target = _table_ref(client, _COMPANY_DAILY_STATS_TABLE_NAME)
    tmp_table_id = _table_ref(client, f"{_COMPANY_DAILY_STATS_TABLE_NAME}_tmp_{uuid.uuid4().hex[:8]}")

    try:
        job_config = bigquery.LoadJobConfig(
            schema=_COMPANY_DAILY_STATS_SCHEMA,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        )
        tmp_table = bigquery.Table(tmp_table_id, schema=_COMPANY_DAILY_STATS_SCHEMA)
        from datetime import timezone as _tz
        tmp_table.expires = datetime.now(_tz.utc) + timedelta(hours=24)
        # create_table sets the 24h expiry; CREATE_IF_NEEDED in LoadJobConfig cannot
        client.create_table(tmp_table, exists_ok=True)

        load_job = client.load_table_from_json(rows, tmp_table_id, job_config=job_config)
        load_job.result()
        if load_job.errors:
            raise BigQueryError(f"merge_company_daily_stats load failed: {load_job.errors}")

        merge_sql = f"""
            MERGE `{target}` T
            USING `{tmp_table_id}` S
            ON T.ticker = S.ticker AND T.snapshot_date = S.snapshot_date
            WHEN MATCHED THEN
              UPDATE SET
                kurs_zamkniecia = S.kurs_zamkniecia,
                zmiana_procentowa = S.zmiana_procentowa,
                zmiana_kwotowa = S.zmiana_kwotowa,
                kurs_otwarcia = S.kurs_otwarcia,
                kurs_min = S.kurs_min,
                kurs_max = S.kurs_max,
                wartosc_obrotu = S.wartosc_obrotu,
                liczba_transakcji = S.liczba_transakcji,
                fetched_at = S.fetched_at,
                source = S.source,
                kurs_odn = S.kurs_odn
            WHEN NOT MATCHED THEN
              INSERT (ticker, snapshot_date, kurs_zamkniecia, zmiana_procentowa,
                      zmiana_kwotowa, kurs_otwarcia, kurs_min, kurs_max,
                      wartosc_obrotu, liczba_transakcji, fetched_at,
                      source, kurs_odn)
              VALUES (S.ticker, S.snapshot_date, S.kurs_zamkniecia, S.zmiana_procentowa,
                      S.zmiana_kwotowa, S.kurs_otwarcia, S.kurs_min, S.kurs_max,
                      S.wartosc_obrotu, S.liczba_transakcji, S.fetched_at,
                      S.source, S.kurs_odn)
        """
        try:
            merge_job = client.query(merge_sql)
            merge_job.result()
        except Exception as exc:
            raise BigQueryError(f"merge_company_daily_stats MERGE failed: {exc}") from exc
        if merge_job.errors:
            raise BigQueryError(f"merge_company_daily_stats MERGE failed: {merge_job.errors}")

        logger.info("merge_company_daily_stats: merged %d rows", len(rows))
    finally:
        try:
            client.delete_table(tmp_table_id, not_found_ok=True)
        except Exception:
            logger.warning(
                "merge_company_daily_stats: failed to clean up temp table %s",
                tmp_table_id,
                exc_info=True,
            )


# ── ETF/ETC/ETN tables (PUL-67) ───────────────────────────────────────────────

_ETF_INSTRUMENTS_TABLE_NAME = "etf_instruments"

_ETF_INSTRUMENTS_SCHEMA = [
    bigquery.SchemaField("ticker", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("isin", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("instrument_type", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
]

_ETF_QUOTES_TABLE_NAME = "etf_quotes"

_ETF_QUOTES_SCHEMA = [
    bigquery.SchemaField("ticker", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("snapshot_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("kurs_zamkniecia", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("zmiana_procentowa", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("zmiana_kwotowa", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("kurs_odn", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("kurs_otwarcia", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("kurs_min", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("kurs_max", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("wolumen_skum", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("fetched_at", "TIMESTAMP", mode="REQUIRED"),
]


def create_etf_instruments_table_if_not_exists() -> None:
    """Create the etf_instruments table in BigQuery if it does not already exist."""
    client = _get_client()
    table_id = _table_ref(client, _ETF_INSTRUMENTS_TABLE_NAME)
    try:
        client.get_table(table_id)
        logger.info("BQ table already exists: %s", table_id)
    except NotFound:
        table = bigquery.Table(table_id, schema=_ETF_INSTRUMENTS_SCHEMA)
        table.clustering_fields = ["ticker"]
        client.create_table(table)
        logger.info("BQ table created: %s", table_id)


def create_etf_quotes_table_if_not_exists() -> None:
    """Create the etf_quotes table in BigQuery if it does not already exist.

    Partitioned by snapshot_date (DAY), clustered by ticker.
    """
    client = _get_client()
    table_id = _table_ref(client, _ETF_QUOTES_TABLE_NAME)
    try:
        client.get_table(table_id)
        logger.info("BQ table already exists: %s", table_id)
    except NotFound:
        table = bigquery.Table(table_id, schema=_ETF_QUOTES_SCHEMA)
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="snapshot_date",
        )
        table.clustering_fields = ["ticker"]
        client.create_table(table)
        logger.info("BQ table created: %s", table_id)


def list_etf_instruments_for_autocomplete() -> list[dict]:
    """Return all ETF/ETC/ETN instruments as list of {ticker, name, instrument_type}."""
    client = _get_client()
    query = f"""
        SELECT ticker, name, instrument_type
        FROM `{_table_ref(client, _ETF_INSTRUMENTS_TABLE_NAME)}`
        ORDER BY ticker
    """
    try:
        rows = list(client.query(query).result())
    except Exception as exc:
        raise BigQueryError(f"list_etf_instruments_for_autocomplete failed: {exc}") from exc
    return [{"ticker": row.ticker, "name": row.name, "instrument_type": row.instrument_type} for row in rows]


def ensure_etf_instruments_schema_current() -> None:
    """Migrate the etf_instruments table — add any missing schema columns."""
    ensure_schema_current(_ETF_INSTRUMENTS_TABLE_NAME, _ETF_INSTRUMENTS_SCHEMA)


def ensure_etf_quotes_schema_current() -> None:
    """Migrate the etf_quotes table — add any missing schema columns."""
    ensure_schema_current(_ETF_QUOTES_TABLE_NAME, _ETF_QUOTES_SCHEMA)


def merge_etf_instruments(rows: list[dict]) -> None:
    """Atomically upsert etf_instruments rows via BigQuery MERGE (ON ticker)."""
    if not rows:
        logger.info("merge_etf_instruments: no rows to merge")
        return

    client = _get_client()
    target = _table_ref(client, _ETF_INSTRUMENTS_TABLE_NAME)
    tmp_table_id = _table_ref(client, f"{_ETF_INSTRUMENTS_TABLE_NAME}_tmp_{uuid.uuid4().hex[:8]}")

    try:
        job_config = bigquery.LoadJobConfig(
            schema=_ETF_INSTRUMENTS_SCHEMA,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        )
        tmp_table = bigquery.Table(tmp_table_id, schema=_ETF_INSTRUMENTS_SCHEMA)
        from datetime import timezone as _tz
        tmp_table.expires = datetime.now(_tz.utc) + timedelta(hours=24)
        client.create_table(tmp_table, exists_ok=True)

        load_job = client.load_table_from_json(rows, tmp_table_id, job_config=job_config)
        load_job.result()
        if load_job.errors:
            raise BigQueryError(f"merge_etf_instruments load failed: {load_job.errors}")

        merge_sql = f"""
            MERGE `{target}` T
            USING `{tmp_table_id}` S
            ON T.ticker = S.ticker
            WHEN MATCHED THEN
              UPDATE SET
                name = S.name,
                isin = S.isin,
                instrument_type = S.instrument_type,
                updated_at = S.updated_at
            WHEN NOT MATCHED THEN
              INSERT (ticker, name, isin, instrument_type, created_at, updated_at)
              VALUES (S.ticker, S.name, S.isin, S.instrument_type, S.created_at, S.updated_at)
        """
        try:
            merge_job = client.query(merge_sql)
            merge_job.result()
        except Exception as exc:
            raise BigQueryError(f"merge_etf_instruments MERGE failed: {exc}") from exc
        if merge_job.errors:
            raise BigQueryError(f"merge_etf_instruments MERGE failed: {merge_job.errors}")

        logger.info("merge_etf_instruments: merged %d rows", len(rows))
    finally:
        try:
            client.delete_table(tmp_table_id, not_found_ok=True)
        except Exception:
            logger.warning("merge_etf_instruments: failed to clean up temp table %s", tmp_table_id, exc_info=True)


def merge_etf_quotes(rows: list[dict]) -> None:
    """Atomically upsert etf_quotes rows via BigQuery MERGE (ON ticker + snapshot_date)."""
    if not rows:
        logger.info("merge_etf_quotes: no rows to merge")
        return

    client = _get_client()
    target = _table_ref(client, _ETF_QUOTES_TABLE_NAME)
    tmp_table_id = _table_ref(client, f"{_ETF_QUOTES_TABLE_NAME}_tmp_{uuid.uuid4().hex[:8]}")

    try:
        job_config = bigquery.LoadJobConfig(
            schema=_ETF_QUOTES_SCHEMA,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        )
        tmp_table = bigquery.Table(tmp_table_id, schema=_ETF_QUOTES_SCHEMA)
        from datetime import timezone as _tz
        tmp_table.expires = datetime.now(_tz.utc) + timedelta(hours=24)
        client.create_table(tmp_table, exists_ok=True)

        load_job = client.load_table_from_json(rows, tmp_table_id, job_config=job_config)
        load_job.result()
        if load_job.errors:
            raise BigQueryError(f"merge_etf_quotes load failed: {load_job.errors}")

        merge_sql = f"""
            MERGE `{target}` T
            USING `{tmp_table_id}` S
            ON T.ticker = S.ticker AND T.snapshot_date = S.snapshot_date
            WHEN MATCHED THEN
              UPDATE SET
                kurs_zamkniecia = S.kurs_zamkniecia,
                zmiana_procentowa = S.zmiana_procentowa,
                zmiana_kwotowa = S.zmiana_kwotowa,
                kurs_odn = S.kurs_odn,
                kurs_otwarcia = S.kurs_otwarcia,
                kurs_min = S.kurs_min,
                kurs_max = S.kurs_max,
                wolumen_skum = S.wolumen_skum,
                fetched_at = S.fetched_at
            WHEN NOT MATCHED THEN
              INSERT (ticker, snapshot_date, kurs_zamkniecia, zmiana_procentowa,
                      zmiana_kwotowa, kurs_odn, kurs_otwarcia, kurs_min, kurs_max,
                      wolumen_skum, fetched_at)
              VALUES (S.ticker, S.snapshot_date, S.kurs_zamkniecia, S.zmiana_procentowa,
                      S.zmiana_kwotowa, S.kurs_odn, S.kurs_otwarcia, S.kurs_min, S.kurs_max,
                      S.wolumen_skum, S.fetched_at)
        """
        try:
            merge_job = client.query(merge_sql)
            merge_job.result()
        except Exception as exc:
            raise BigQueryError(f"merge_etf_quotes MERGE failed: {exc}") from exc
        if merge_job.errors:
            raise BigQueryError(f"merge_etf_quotes MERGE failed: {merge_job.errors}")

        logger.info("merge_etf_quotes: merged %d rows", len(rows))
    finally:
        try:
            client.delete_table(tmp_table_id, not_found_ok=True)
        except Exception:
            logger.warning("merge_etf_quotes: failed to clean up temp table %s", tmp_table_id, exc_info=True)


# ── Insert-only MERGE for historical backfill (PUL-92) ────────────────────────


def _merge_insert_only(
    fn_name: str,
    table_name: str,
    schema: list,
    columns: list[str],
    rows: list[dict],
    key_columns: tuple[str, ...] = ("ticker", "snapshot_date"),
    order_column: str = "fetched_at",
) -> int:
    """Insert rows via MERGE with no WHEN MATCHED branch — existing rows keyed on
    `key_columns` are never touched, so backfilled data can never overwrite
    scraper-written rows. The source is deduped inside the MERGE (QUALIFY):
    WHEN NOT MATCHED fires per source row, so a duplicated batch key would
    otherwise insert twice. Returns the number of inserted rows.

    The defaults reproduce the original (ticker, snapshot_date)/fetched_at
    behaviour byte for byte, so the two daily-stats callers are unaffected.
    """
    if not rows:
        logger.info("%s: no rows to merge", fn_name)
        return 0

    client = _get_client()
    target = _table_ref(client, table_name)
    tmp_table_id = _table_ref(client, f"{table_name}_tmp_{uuid.uuid4().hex[:8]}")

    try:
        job_config = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        )
        tmp_table = bigquery.Table(tmp_table_id, schema=schema)
        from datetime import timezone as _tz
        tmp_table.expires = datetime.now(_tz.utc) + timedelta(hours=24)
        client.create_table(tmp_table, exists_ok=True)

        load_job = client.load_table_from_json(rows, tmp_table_id, job_config=job_config)
        load_job.result()
        if load_job.errors:
            raise BigQueryError(f"{fn_name} load failed: {load_job.errors}")

        cols = ", ".join(columns)
        vals = ", ".join(f"S.{c}" for c in columns)
        # Spacing here is load-bearing: tests/test_bigquery_insert_only_merge.py
        # asserts the literal string "PARTITION BY ticker, snapshot_date".
        partition_by = ", ".join(key_columns)
        on_clause = " AND ".join(f"T.{c} = S.{c}" for c in key_columns)
        merge_sql = f"""
            MERGE `{target}` T
            USING (
              SELECT * FROM `{tmp_table_id}`
              QUALIFY ROW_NUMBER() OVER (PARTITION BY {partition_by} ORDER BY {order_column} DESC) = 1
            ) S
            ON {on_clause}
            WHEN NOT MATCHED THEN
              INSERT ({cols})
              VALUES ({vals})
        """
        try:
            merge_job = client.query(merge_sql)
            merge_job.result()
        except Exception as exc:
            raise BigQueryError(f"{fn_name} MERGE failed: {exc}") from exc
        if merge_job.errors:
            raise BigQueryError(f"{fn_name} MERGE failed: {merge_job.errors}")

        inserted = int(merge_job.num_dml_affected_rows or 0)
        logger.info("%s: inserted %d of %d rows", fn_name, inserted, len(rows))
        return inserted
    finally:
        try:
            client.delete_table(tmp_table_id, not_found_ok=True)
        except Exception:
            logger.warning("%s: failed to clean up temp table %s", fn_name, tmp_table_id, exc_info=True)


def merge_company_daily_stats_insert_only(rows: list[dict]) -> int:
    """Insert-only MERGE into company_daily_stats; never updates existing rows."""
    return _merge_insert_only(
        "merge_company_daily_stats_insert_only",
        _COMPANY_DAILY_STATS_TABLE_NAME,
        _COMPANY_DAILY_STATS_SCHEMA,
        [
            "ticker", "snapshot_date", "kurs_zamkniecia", "zmiana_procentowa",
            "zmiana_kwotowa", "kurs_otwarcia", "kurs_min", "kurs_max",
            "wartosc_obrotu", "liczba_transakcji", "fetched_at",
            "source", "kurs_odn",
        ],
        rows,
    )


def merge_etf_quotes_insert_only(rows: list[dict]) -> int:
    """Insert-only MERGE into etf_quotes; never updates existing rows."""
    return _merge_insert_only(
        "merge_etf_quotes_insert_only",
        _ETF_QUOTES_TABLE_NAME,
        _ETF_QUOTES_SCHEMA,
        [
            "ticker", "snapshot_date", "kurs_zamkniecia", "zmiana_procentowa",
            "zmiana_kwotowa", "kurs_odn", "kurs_otwarcia", "kurs_min", "kurs_max",
            "wolumen_skum", "fetched_at",
        ],
        rows,
    )


_CLOSE_CORRECTION_COLUMNS = ("kurs_zamkniecia", "zmiana_procentowa", "zmiana_kwotowa", "source")


def merge_company_daily_stats_close_correction(rows: list[dict]) -> int:
    """Update-only MERGE that repairs a close written from the wrong source (PUL-98).

    Two deliberate narrowings make this safe to run over history:

    * Only the close and the two values derived from it are updated — plus `source`,
      so the row records where the corrected value came from. Turnover, trade count,
      the OHLC levels and `fetched_at` describe the session as it was observed; a
      correction has no better knowledge of them and must not overwrite them.
    * There is **no WHEN NOT MATCHED branch**. The trading-day spine is
      `SELECT DISTINCT snapshot_date` from this table, so inserting a date the table
      never carried would silently redefine what counts as a session day.

    Source rows are deduped inside the MERGE (newest `fetched_at` wins) so a batch
    that names the same (ticker, snapshot_date) twice is not a non-deterministic
    update. Returns the number of rows actually changed; rows whose key is absent
    from the table simply do not count towards it.
    """
    if not rows:
        logger.info("merge_company_daily_stats_close_correction: no rows to merge")
        return 0

    client = _get_client()
    target = _table_ref(client, _COMPANY_DAILY_STATS_TABLE_NAME)
    tmp_table_id = _table_ref(client, f"{_COMPANY_DAILY_STATS_TABLE_NAME}_tmp_{uuid.uuid4().hex[:8]}")

    try:
        job_config = bigquery.LoadJobConfig(
            schema=_COMPANY_DAILY_STATS_SCHEMA,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        )
        tmp_table = bigquery.Table(tmp_table_id, schema=_COMPANY_DAILY_STATS_SCHEMA)
        from datetime import timezone as _tz
        tmp_table.expires = datetime.now(_tz.utc) + timedelta(hours=24)
        client.create_table(tmp_table, exists_ok=True)

        load_job = client.load_table_from_json(rows, tmp_table_id, job_config=job_config)
        load_job.result()
        if load_job.errors:
            raise BigQueryError(
                f"merge_company_daily_stats_close_correction load failed: {load_job.errors}"
            )

        assignments = ",\n                ".join(
            f"{c} = S.{c}" for c in _CLOSE_CORRECTION_COLUMNS
        )
        merge_sql = f"""
            MERGE `{target}` T
            USING (
              SELECT * FROM `{tmp_table_id}`
              QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker, snapshot_date ORDER BY fetched_at DESC) = 1
            ) S
            ON T.ticker = S.ticker AND T.snapshot_date = S.snapshot_date
            WHEN MATCHED THEN
              UPDATE SET
                {assignments}
        """
        try:
            merge_job = client.query(merge_sql)
            merge_job.result()
        except Exception as exc:
            raise BigQueryError(
                f"merge_company_daily_stats_close_correction MERGE failed: {exc}"
            ) from exc
        if merge_job.errors:
            raise BigQueryError(
                f"merge_company_daily_stats_close_correction MERGE failed: {merge_job.errors}"
            )

        corrected = int(merge_job.num_dml_affected_rows or 0)
        logger.info(
            "merge_company_daily_stats_close_correction: corrected %d of %d rows",
            corrected,
            len(rows),
        )
        return corrected
    finally:
        try:
            client.delete_table(tmp_table_id, not_found_ok=True)
        except Exception:
            logger.warning(
                "merge_company_daily_stats_close_correction: failed to clean up temp table %s",
                tmp_table_id,
                exc_info=True,
            )


def get_latest_company_stats_fetched_at(snapshot_date: date) -> str | None:
    """Return the newest fetched_at ISO string in company_daily_stats for snapshot_date.

    Returns None if no data exists for that date, or if the date's rows carry no
    fetched_at at all.
    Raises BigQueryError on query failure.

    PUL-113: `MAX`, not `LIMIT 1`. The old form returned an arbitrary row, so a
    single NULL among ~730 rows decided the answer — and it decided it badly:
    `str(None)` reached the frontend as the truthy string "None", became an
    Invalid Date and rendered the timestamp as "NaN:NaN". Aggregating also skips
    NULLs by definition, so the only way back is a genuinely empty column, which
    is what None is for.
    """
    client = _get_client()
    table = _table_ref(client, _COMPANY_DAILY_STATS_TABLE_NAME)
    query = f"""
        SELECT MAX(fetched_at) AS fetched_at
        FROM `{table}`
        WHERE snapshot_date = @snapshot_date
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("snapshot_date", "DATE", snapshot_date)]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"get_latest_company_stats_fetched_at failed: {exc}") from exc
    # An aggregate always returns one row; the value inside it is NULL when the
    # date has no rows, so the emptiness test has to be on the value, not the list.
    val = rows[0].fetched_at if rows else None
    if val is None:
        return None
    return val.isoformat() if hasattr(val, "isoformat") else str(val)


def get_previous_session_closes(before: date) -> tuple[date | None, dict[str, float | None]]:
    """Return (session_date, {ticker: kurs_zamkniecia}) for the newest session before `before`.

    "Session" here means a date this table actually holds — the same spine every
    reader uses. Returns (None, {}) when there is no earlier date at all, which is
    the empty-table case, not an error.

    Raises BigQueryError on query failure.
    """
    client = _get_client()
    table = _table_ref(client, _COMPANY_DAILY_STATS_TABLE_NAME)
    query = f"""
        WITH previous AS (
          SELECT MAX(snapshot_date) AS session_date
          FROM `{table}`
          WHERE snapshot_date < @before
        )
        SELECT s.snapshot_date, s.ticker, s.kurs_zamkniecia
        FROM `{table}` s
        JOIN previous p ON s.snapshot_date = p.session_date
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("before", "DATE", before)]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"get_previous_session_closes failed: {exc}") from exc

    if not rows:
        return None, {}
    return rows[0].snapshot_date, {r.ticker: r.kurs_zamkniecia for r in rows}


# ── notification subscriptions (PUL-81 slice a) ───────────────────────────────

_NOTIFICATION_SUBSCRIPTIONS_TABLE_NAME = "notification_subscriptions"

_NOTIFICATION_SUBSCRIPTIONS_SCHEMA = [
    bigquery.SchemaField("user_id",      "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("email",        "STRING",    mode="NULLABLE"),
    # min_score is stored for slice (b)'s delivery cron; not surfaced in the UI.
    bigquery.SchemaField("min_score",    "INT64",     mode="NULLABLE"),
    bigquery.SchemaField("enabled",      "BOOL",      mode="REQUIRED"),
    # confirmed_at is informational (no double opt-in in slice a — the account
    # email is already verified). enabled is the authoritative opt-in flag.
    bigquery.SchemaField("confirmed_at", "TIMESTAMP", mode="NULLABLE"),
    bigquery.SchemaField("updated_at",   "TIMESTAMP", mode="NULLABLE"),
]


def create_notification_subscriptions_table_if_not_exists() -> None:
    """Create the notification_subscriptions table in BigQuery if absent."""
    client = _get_client()
    table_id = _table_ref(client, _NOTIFICATION_SUBSCRIPTIONS_TABLE_NAME)
    try:
        client.get_table(table_id)
        logger.info("BQ table already exists: %s", table_id)
    except NotFound:
        table = bigquery.Table(table_id, schema=_NOTIFICATION_SUBSCRIPTIONS_SCHEMA)
        client.create_table(table)
        logger.info("BQ table created: %s", table_id)


def ensure_notification_subscriptions_schema_current() -> None:
    """Migrate the notification_subscriptions table — add any missing columns."""
    ensure_schema_current(
        _NOTIFICATION_SUBSCRIPTIONS_TABLE_NAME, _NOTIFICATION_SUBSCRIPTIONS_SCHEMA
    )


def get_notification_settings(user_id: str) -> dict:
    """Read a user's email-notification preference.

    Returns the opt-in default (enabled=False) when no row exists — reading a
    preference must never fail just because the user has never set one. Raises
    BigQueryError only on an actual query failure.
    """
    client = _get_client()
    query = f"""
        SELECT enabled, email, min_score, confirmed_at
        FROM `{_table_ref(client, _NOTIFICATION_SUBSCRIPTIONS_TABLE_NAME)}`
        WHERE user_id = @user_id
        LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"get_notification_settings failed: {exc}") from exc
    if not rows:
        return {"enabled": False, "email": None, "min_score": 0, "confirmed_at": None}
    row = rows[0]
    return {
        "enabled": bool(row.enabled),
        "email": row.email,
        "min_score": row.min_score if row.min_score is not None else 0,
        "confirmed_at": row.confirmed_at,
    }


def upsert_notification_settings(
    user_id: str, email: str | None, enabled: bool, min_score: int = 0
) -> None:
    """Persist a user's email-notification preference (MERGE on user_id).

    enabled is the authoritative opt-in flag. confirmed_at is stamped with
    CURRENT_TIMESTAMP() on first enable and preserved thereafter (informational,
    since the account email is already verified — no double opt-in). Raises
    BigQueryError on failure.
    """
    client = _get_client()
    query = f"""
        MERGE `{_table_ref(client, _NOTIFICATION_SUBSCRIPTIONS_TABLE_NAME)}` T
        USING (
            SELECT @user_id AS user_id, @email AS email,
                   @enabled AS enabled, @min_score AS min_score
        ) S
        ON T.user_id = S.user_id
        WHEN MATCHED THEN
          UPDATE SET
            enabled      = S.enabled,
            email        = S.email,
            min_score    = S.min_score,
            confirmed_at = CASE WHEN S.enabled
                                THEN COALESCE(T.confirmed_at, CURRENT_TIMESTAMP())
                                ELSE T.confirmed_at END,
            updated_at   = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN
          INSERT (user_id, email, min_score, enabled, confirmed_at, updated_at)
          VALUES (
            S.user_id, S.email, S.min_score, S.enabled,
            CASE WHEN S.enabled THEN CURRENT_TIMESTAMP() ELSE NULL END,
            CURRENT_TIMESTAMP()
          )
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id",   "STRING", user_id),
            bigquery.ScalarQueryParameter("email",     "STRING", email),
            bigquery.ScalarQueryParameter("enabled",   "BOOL",   enabled),
            bigquery.ScalarQueryParameter("min_score", "INT64",  min_score),
        ]
    )
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"upsert_notification_settings failed: {exc}") from exc
    if job.errors:
        raise BigQueryError(f"upsert_notification_settings failed: {job.errors}")
    logger.debug("upsert_notification_settings: user_id=%s enabled=%s", user_id, enabled)


# ── broker-export operations: raw source of truth for the import (PUL-95) ─────

_USER_BROKER_OPERATIONS_TABLE_NAME = "user_broker_operations"

# Raw operations as the source of truth: positions and dividends are projections
# over this table. Only identity and the fields EVERY broker must supply are
# REQUIRED — anything a future broker might not carry stays NULLABLE.
_USER_BROKER_OPERATIONS_SCHEMA = [
    bigquery.SchemaField("user_id",         "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("portfolio_id",    "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("broker",          "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("external_id",     "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("op_type",         "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("occurred_at",     "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("imported_at",     "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("amount_pln",      "FLOAT64",   mode="REQUIRED"),
    bigquery.SchemaField("raw_type",        "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("ticker",          "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("instrument_name", "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("volume",          "FLOAT64",   mode="NULLABLE"),
    bigquery.SchemaField("unit_price",      "FLOAT64",   mode="NULLABLE"),
    bigquery.SchemaField("comment",         "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("source_file",     "STRING",    mode="NULLABLE"),
]

_USER_BROKER_OPERATIONS_COLUMNS = [field.name for field in _USER_BROKER_OPERATIONS_SCHEMA]

# Clustering is chosen once and for good: ensure_schema_current only appends
# columns, so it cannot be migrated afterwards. Every read is scoped to one user
# and usually to one ticker. Unlike the other per-user tables, which carry no
# clustering at all, this one only ever grows — one row per broker operation.
_USER_BROKER_OPERATIONS_CLUSTERING = ["user_id", "ticker"]


def create_user_broker_operations_table_if_not_exists() -> None:
    """Create the user_broker_operations table in BigQuery if absent."""
    client = _get_client()
    table_id = _table_ref(client, _USER_BROKER_OPERATIONS_TABLE_NAME)
    try:
        client.get_table(table_id)
        logger.info("BQ table already exists: %s", table_id)
    except NotFound:
        table = bigquery.Table(table_id, schema=_USER_BROKER_OPERATIONS_SCHEMA)
        table.clustering_fields = _USER_BROKER_OPERATIONS_CLUSTERING
        client.create_table(table)
        logger.info("BQ table created: %s", table_id)


def ensure_user_broker_operations_schema_current() -> None:
    """Migrate user_broker_operations — add any missing schema columns."""
    ensure_schema_current(_USER_BROKER_OPERATIONS_TABLE_NAME, _USER_BROKER_OPERATIONS_SCHEMA)


def merge_user_broker_operations(rows: list[dict]) -> int:
    """Idempotently store a batch of broker operations; returns how many were new.

    Keyed on external_id ("{broker}:{ID}"), which comes straight from the
    export's own ID column — filled in all 571 rows of the real files, unique
    within a file and non-colliding across files, so no content hash is needed.

    There is no WHEN MATCHED branch: an operation already stored is untouchable,
    which is what makes re-importing the same file a no-op.
    """
    return _merge_insert_only(
        "merge_user_broker_operations",
        _USER_BROKER_OPERATIONS_TABLE_NAME,
        _USER_BROKER_OPERATIONS_SCHEMA,
        _USER_BROKER_OPERATIONS_COLUMNS,
        rows,
        key_columns=("external_id",),
        order_column="imported_at",
    )


def list_broker_trades(user_id: str, portfolio_id: str | None = None) -> list[dict]:
    """Every stored buy/sell for the user, oldest first, for FIFO matching.

    Returns plain dicts shaped for ``compute_realized_pnl``. The whole history is
    returned with no year filter on purpose: FIFO has to walk every trade to know
    what the shares cost, and narrowing the rows first would leave later sales
    matched against nothing. A real account is a couple of hundred rows, so this
    is one small scan rather than something worth pushing into SQL.

    ``portfolio_id=None`` spans every wallet of the user (the "Wszystkie" view).
    Raises BigQueryError on failure.
    """
    client = _get_client()
    _t = time.time()
    table = _table_ref(client, _USER_BROKER_OPERATIONS_TABLE_NAME)
    portfolio_filter = "AND portfolio_id = @portfolio_id" if portfolio_id is not None else ""
    query = f"""
        SELECT ticker, op_type, occurred_at, volume, unit_price, instrument_name
        FROM `{table}`
        WHERE user_id = @user_id {portfolio_filter}
          AND op_type IN ('buy', 'sell')
          AND ticker IS NOT NULL
        ORDER BY occurred_at
    """
    params = [bigquery.ScalarQueryParameter("user_id", "STRING", user_id)]
    if portfolio_id is not None:
        params.append(bigquery.ScalarQueryParameter("portfolio_id", "STRING", portfolio_id))
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"list_broker_trades failed: {exc}") from exc
    logger.debug("BQ list_broker_trades: %.0fms", (time.time() - _t) * 1000)
    return [dict(row) for row in rows]


def get_dividend_summary(
    user_id: str, portfolio_id: str | None = None, year: int | None = None
) -> dict:
    """Cash-dividend totals, per-company breakdown, and the list of years.

    `portfolio_id=None` spans every wallet of the user; `year=None` spans every
    year. Gross and tax are summed from two distinct op_types and never paired
    up row by row — on the real exports that pairing fails in 24 cases.

    The year list rides along on the SAME query, built meta-first
    (`FROM meta LEFT JOIN data`). Written the other way round the selector goes
    empty as soon as the chosen year has no payouts, stranding the user on a
    year they then cannot leave — the PUL-100 lesson.

    Periods are extracted in `Europe/Warsaw`, not UTC. `occurred_at` stores a
    true UTC instant (the real history's trades sit at 7-15 UTC, i.e. the GPW
    session in CEST), so a payout credited just after midnight Warsaw time falls
    in the previous UTC day — and, once a year, the previous UTC year (PUL-120).
    """
    client = _get_client()
    table = _table_ref(client, _USER_BROKER_OPERATIONS_TABLE_NAME)
    query = f"""
        WITH scoped AS (
            SELECT
                EXTRACT(YEAR FROM occurred_at AT TIME ZONE 'Europe/Warsaw') AS year,
                ticker,
                op_type,
                amount_pln
            FROM `{table}`
            WHERE user_id = @user_id
              AND op_type IN ('dividend', 'withholding_tax')
              AND (@portfolio_id IS NULL OR portfolio_id = @portfolio_id)
        ),
        meta AS (
            SELECT ARRAY_AGG(DISTINCT year ORDER BY year) AS all_years
            FROM scoped
        ),
        data AS (
            -- Grouped by ticker alone. Adding `year` here splits a holding into
            -- one row per year whenever the caller spans every year, so the
            -- breakdown would list KRU three times and understate each row.
            SELECT
                ticker,
                SUM(IF(op_type = 'dividend', amount_pln, 0)) AS gross,
                SUM(IF(op_type = 'withholding_tax', amount_pln, 0)) AS tax,
                COUNTIF(op_type = 'dividend') AS payouts
            FROM scoped
            WHERE (@year IS NULL OR year = @year)
            GROUP BY ticker
        )
        SELECT meta.all_years, data.ticker, data.gross, data.tax, data.payouts
        FROM meta
        LEFT JOIN data ON TRUE
        ORDER BY data.gross DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
            bigquery.ScalarQueryParameter("portfolio_id", "STRING", portfolio_id),
            bigquery.ScalarQueryParameter("year", "INT64", year),
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"get_dividend_summary failed: {exc}") from exc

    years: list[int] = []
    by_ticker: list[dict] = []
    gross_total = tax_total = 0.0
    count_total = 0
    for row in rows:
        if not years:
            # ARRAY_AGG over zero rows yields [] rather than None.
            years = [int(y) for y in (row["all_years"] or [])]
        # The metadata row survives the join even with no data behind it; drop it.
        if row["ticker"] is None:
            continue
        gross = float(row["gross"] or 0.0)
        tax = float(row["tax"] or 0.0)
        payouts = int(row["payouts"] or 0)
        gross_total += gross
        tax_total += tax
        count_total += payouts
        by_ticker.append({
            "ticker": row["ticker"],
            "gross": gross,
            "tax": tax,
            "net": gross + tax,
            # Named to match the SQL column and what the renderer reads. Emitting
            # `count` here left the "Wypłat" column showing zero on real data
            # while every fake happily supplied `payouts`.
            "payouts": payouts,
        })

    return {
        "years": years,
        "totals": {
            "gross": gross_total,
            "tax": tax_total,
            "net": gross_total + tax_total,
            "count": count_total,
        },
        "by_ticker": by_ticker,
    }


# ── notification delivery: sent-log + recipient select (PUL-81 slice b) ───────

_NOTIFICATION_SENT_LOG_TABLE_NAME = "notification_sent_log"

_NOTIFICATION_SENT_LOG_SCHEMA = [
    bigquery.SchemaField("user_id",         "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("announcement_id", "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("email",           "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("sent_at",         "TIMESTAMP", mode="REQUIRED"),
]


def create_notification_sent_log_table_if_not_exists() -> None:
    """Create the notification_sent_log dedup table in BigQuery if absent."""
    client = _get_client()
    table_id = _table_ref(client, _NOTIFICATION_SENT_LOG_TABLE_NAME)
    try:
        client.get_table(table_id)
        logger.info("BQ table already exists: %s", table_id)
    except NotFound:
        table = bigquery.Table(table_id, schema=_NOTIFICATION_SENT_LOG_SCHEMA)
        client.create_table(table)
        logger.info("BQ table created: %s", table_id)


def ensure_notification_sent_log_schema_current() -> None:
    """Migrate the notification_sent_log table — add any missing columns."""
    ensure_schema_current(_NOTIFICATION_SENT_LOG_TABLE_NAME, _NOTIFICATION_SENT_LOG_SCHEMA)


def select_recipients_for_announcement(announcement_id: str) -> list[dict]:
    """Opted-in watchers who should be emailed about ONE announcement, not yet sent.

    The event-driven recipient query, scoped to a single announcement_id (no time
    window) — the ingestion hook in `main.py` calls it per announcement. A user
    qualifies when they watch the
    announcement's ticker, their subscription is enabled with an email, the
    announcement is approved + scored at/above their min_score, it was published
    after they opted in (confirmed_at floor), and the (user, announcement) pair is
    not already in the sent-log. Returns one {user_id, email} per recipient; empty
    when none qualify. Raises BigQueryError on failure.
    """
    client = _get_client()
    query = f"""
        SELECT ns.user_id AS user_id, ns.email AS email
        FROM `{_table_ref(client, _TABLE_NAME)}` AS a
        JOIN `{_table_ref(client, _WATCHLIST_TABLE_NAME)}` AS w
          ON w.ticker = a.ticker
        JOIN `{_table_ref(client, _NOTIFICATION_SUBSCRIPTIONS_TABLE_NAME)}` AS ns
          ON ns.user_id = w.user_id
        WHERE a.announcement_id = @announcement_id
          AND a.analysis_approved = TRUE
          AND a.analysis_score IS NOT NULL
          AND ns.enabled = TRUE
          AND ns.email IS NOT NULL
          AND a.analysis_score >= COALESCE(ns.min_score, 0)
          AND a.published_at >= COALESCE(ns.confirmed_at, ns.updated_at)
          AND NOT EXISTS (
              SELECT 1 FROM `{_table_ref(client, _NOTIFICATION_SENT_LOG_TABLE_NAME)}` AS l
              WHERE l.user_id = ns.user_id AND l.announcement_id = a.announcement_id
          )
        ORDER BY ns.user_id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("announcement_id", "STRING", announcement_id),
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"select_recipients_for_announcement failed: {exc}") from exc
    return [{"user_id": row.user_id, "email": row.email} for row in rows]


def record_notification_sent(user_id: str, announcement_id: str, email: str | None) -> None:
    """Mark a (user, announcement) pair as emailed — idempotent.

    INSERT…WHERE NOT EXISTS so re-running with the same pair is a silent no-op
    (the dedup key is enforced here, since BigQuery has no unique constraint).
    Raises BigQueryError on failure.
    """
    client = _get_client()
    table = _table_ref(client, _NOTIFICATION_SENT_LOG_TABLE_NAME)
    query = f"""
        INSERT INTO `{table}` (user_id, announcement_id, email, sent_at)
        SELECT @user_id, @announcement_id, @email, CURRENT_TIMESTAMP()
        FROM (SELECT 1)
        WHERE NOT EXISTS (
            SELECT 1 FROM `{table}`
            WHERE user_id = @user_id AND announcement_id = @announcement_id
        )
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id",         "STRING", user_id),
            bigquery.ScalarQueryParameter("announcement_id", "STRING", announcement_id),
            bigquery.ScalarQueryParameter("email",           "STRING", email),
        ]
    )
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"record_notification_sent failed: {exc}") from exc
    if job.errors:
        raise BigQueryError(f"record_notification_sent failed: {job.errors}")
    logger.debug("record_notification_sent: user_id=%s announcement_id=%s", user_id, announcement_id)
