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
import hashlib
import logging
import os
import threading
import uuid
from datetime import date, datetime, timedelta
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
        return None
    row = rows[0]
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
        return None
    row = rows[0]
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


_WATCHLIST_TABLE_NAME = "watchlist"

_WATCHLIST_SCHEMA = [
    bigquery.SchemaField("client_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("ticker", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("added_at", "TIMESTAMP", mode="REQUIRED"),
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
    """Migrate the watchlist table — add any missing schema columns.

    Thin binding over `ensure_schema_current()`; idempotent and safe to call on
    every API service startup (cold start of every Cloud Run instance).
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
        ON T.portfolio_id = S.portfolio_id AND T.ticker = S.ticker
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


def list_user_portfolio_positions(user_id: str, portfolio_id: str | None = None) -> list[dict]:
    """Return positions for user_id joined with the latest available close price.

    When portfolio_id is provided, results are scoped to that wallet. Without it,
    all positions for the user are returned (used by the treemap endpoint).
    Uses ROW_NUMBER() OVER PARTITION BY ticker to pick the most recent company_daily_stats
    entry per ticker, then LEFT JOIN so positions without price data still appear.
    Raises BigQueryError on query failure.
    """
    client = _get_client()
    portfolio_filter = "AND p.portfolio_id = @portfolio_id" if portfolio_id is not None else ""
    query = f"""
        WITH latest_stats AS (
          SELECT
            ticker,
            kurs_zamkniecia,
            zmiana_procentowa,
            CAST(snapshot_date AS STRING) AS price_as_of,
            ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) AS rn
          FROM `{_table_ref(client, _COMPANY_DAILY_STATS_TABLE_NAME)}`
        )
        SELECT
          p.ticker,
          p.company_name,
          p.shares,
          p.avg_buy_price,
          ls.kurs_zamkniecia   AS current_price,
          ls.zmiana_procentowa AS daily_change_pct,
          ls.price_as_of
        FROM `{_table_ref(client, _USER_PORTFOLIO_POSITIONS_TABLE_NAME)}` p
        LEFT JOIN latest_stats ls
          ON p.ticker = ls.ticker AND ls.rn = 1
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
    return [dict(row) for row in rows]


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

    Display order is determined by type; for "inny", queries existing count
    to assign 4 (first) or 5 (second).
    Raises BigQueryError on failure.
    """
    if portfolio_type == "inny":
        existing = list_user_portfolios(user_id)
        inny_count = sum(1 for p in existing if p["portfolio_type"] == "inny")
        display_order = 4 if inny_count == 0 else 5
    else:
        display_order = _PORTFOLIO_DISPLAY_ORDER.get(portfolio_type, 99)

    portfolio_id = str(uuid.uuid4())
    client = _get_client()
    query = f"""
        INSERT INTO `{_table_ref(client, _USER_PORTFOLIOS_TABLE_NAME)}`
          (user_id, portfolio_id, portfolio_type, portfolio_name, display_order, created_at)
        VALUES
          (@user_id, @portfolio_id, @portfolio_type, @portfolio_name, @display_order,
           CURRENT_TIMESTAMP())
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id",        "STRING",  user_id),
            bigquery.ScalarQueryParameter("portfolio_id",   "STRING",  portfolio_id),
            bigquery.ScalarQueryParameter("portfolio_type", "STRING",  portfolio_type),
            bigquery.ScalarQueryParameter("portfolio_name", "STRING",  portfolio_name),
            bigquery.ScalarQueryParameter("display_order",  "INTEGER", display_order),
        ]
    )
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"create_user_portfolio failed: {exc}") from exc
    if job.errors:
        raise BigQueryError(f"create_user_portfolio failed: {job.errors}")
    logger.debug("create_user_portfolio: user_id=%s portfolio_id=%s type=%s", user_id, portfolio_id, portfolio_type)
    return portfolio_id


def delete_user_portfolio(user_id: str, portfolio_id: str) -> None:
    """Delete a wallet and cascade-delete all its positions (positions first).

    Raises BigQueryError on query failure.
    """
    client = _get_client()
    pos_query = f"""
        DELETE FROM `{_table_ref(client, _USER_PORTFOLIO_POSITIONS_TABLE_NAME)}`
        WHERE portfolio_id = @portfolio_id
    """
    wallet_query = f"""
        DELETE FROM `{_table_ref(client, _USER_PORTFOLIOS_TABLE_NAME)}`
        WHERE user_id = @user_id AND portfolio_id = @portfolio_id
    """
    params = [
        bigquery.ScalarQueryParameter("user_id",      "STRING", user_id),
        bigquery.ScalarQueryParameter("portfolio_id", "STRING", portfolio_id),
    ]
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    try:
        client.query(pos_query, job_config=job_config).result()
        client.query(wallet_query, job_config=job_config).result()
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


def add_watchlist_ticker(client_id: str, ticker: str) -> None:
    """Add `ticker` to `client_id`'s watchlist; silent no-op if already present.

    Raises BigQueryError if the query job fails.
    """
    client = _get_client()
    query = f"""
        INSERT INTO `{_table_ref(client, _WATCHLIST_TABLE_NAME)}` (client_id, ticker, added_at)
        SELECT @client_id, @ticker, CURRENT_TIMESTAMP()
        FROM (SELECT 1)
        WHERE NOT EXISTS (
            SELECT 1 FROM `{_table_ref(client, _WATCHLIST_TABLE_NAME)}`
            WHERE client_id = @client_id AND ticker = @ticker
        )
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("client_id", "STRING", client_id),
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
    logger.debug("add_watchlist_ticker: client_id=%s ticker=%s", client_id, ticker)


def remove_watchlist_ticker(client_id: str, ticker: str) -> None:
    """Remove `ticker` from `client_id`'s watchlist; no-op if not present.

    Raises BigQueryError if the query job fails.
    """
    client = _get_client()
    query = f"""
        DELETE FROM `{_table_ref(client, _WATCHLIST_TABLE_NAME)}`
        WHERE client_id = @client_id AND ticker = @ticker
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("client_id", "STRING", client_id),
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
    logger.debug("remove_watchlist_ticker: client_id=%s ticker=%s", client_id, ticker)


def list_watchlist_tickers(client_id: str) -> list[str]:
    """Return `client_id`'s watchlisted tickers, most recently added first.

    Raises BigQueryError if the query job fails.
    """
    client = _get_client()
    query = f"""
        SELECT ticker
        FROM `{_table_ref(client, _WATCHLIST_TABLE_NAME)}`
        WHERE client_id = @client_id
        ORDER BY added_at DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("client_id", "STRING", client_id),
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
    offset = (page - 1) * page_size
    where, filter_params = _build_filter_clauses(
        approved_only=False,
        ticker=ticker,
        company=company,
        event_type=event_type,
        from_dt=from_dt,
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
    client_id: str,
    page: int = 1,
    page_size: int = 20,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
) -> list[dict]:
    """Return approved announcements for tickers in `client_id`'s watchlist.

    Same returned column set as `list_announcements_user`. The watchlist
    subquery is bounded to 200 tickers per client — a defensive guardrail,
    not a user-facing limit. Raises BigQueryError on query failure.
    """
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    client = _get_client()
    offset = (page - 1) * page_size
    where, filter_params = _build_filter_clauses(
        approved_only=True,
        from_dt=from_dt,
        to_dt=to_dt,
    )
    query = f"""
        SELECT
            a.company, a.ticker, a.event_type, a.structured_analysis,
            a.published_at
        FROM `{_table_ref(client)}` AS a
        INNER JOIN (
            SELECT ticker FROM `{_table_ref(client, _WATCHLIST_TABLE_NAME)}`
            WHERE client_id = @client_id LIMIT 200
        ) AS w ON a.ticker = w.ticker
        {where}
        ORDER BY a.published_at DESC
        LIMIT @page_size OFFSET @offset
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("client_id", "STRING", client_id),
            bigquery.ScalarQueryParameter("page_size", "INT64", page_size),
            bigquery.ScalarQueryParameter("offset", "INT64", offset),
            *filter_params,
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"list_announcements_for_watchlist failed: {exc}") from exc
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
    """Return sorted list of all tickers in the companies dimension table."""
    client = _get_client()
    query = f"""
        SELECT ticker
        FROM `{_table_ref(client, _COMPANIES_TABLE_NAME)}`
        ORDER BY ticker
    """
    try:
        rows = list(client.query(query).result())
    except Exception as exc:
        raise BigQueryError(f"list_distinct_tickers failed: {exc}") from exc
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
                fetched_at = S.fetched_at
            WHEN NOT MATCHED THEN
              INSERT (ticker, snapshot_date, kurs_zamkniecia, zmiana_procentowa,
                      zmiana_kwotowa, kurs_otwarcia, kurs_min, kurs_max,
                      wartosc_obrotu, liczba_transakcji, fetched_at)
              VALUES (S.ticker, S.snapshot_date, S.kurs_zamkniecia, S.zmiana_procentowa,
                      S.zmiana_kwotowa, S.kurs_otwarcia, S.kurs_min, S.kurs_max,
                      S.wartosc_obrotu, S.liczba_transakcji, S.fetched_at)
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


def get_latest_company_stats_fetched_at(snapshot_date: date) -> str | None:
    """Return fetched_at ISO string for any row in company_daily_stats for snapshot_date.

    Returns None if no data exists for that date.
    Raises BigQueryError on query failure.
    """
    client = _get_client()
    table = _table_ref(client, _COMPANY_DAILY_STATS_TABLE_NAME)
    query = f"""
        SELECT fetched_at
        FROM `{table}`
        WHERE snapshot_date = @snapshot_date
        LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("snapshot_date", "DATE", snapshot_date)]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"get_latest_company_stats_fetched_at failed: {exc}") from exc
    if not rows:
        return None
    val = rows[0].fetched_at
    return val.isoformat() if hasattr(val, "isoformat") else str(val)
