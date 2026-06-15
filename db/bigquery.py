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
from datetime import date, datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from src.exceptions import BigQueryError

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
    """Return top-N approved announcements for a time window, ordered by score DESC.

    Only announcements with `analysis_score >= min_score` qualify (PUL-27 quality
    gate). Filtering at fetch time gates the WHOLE pipeline (generation + email +
    publish): an empty pool after filtering routes to the existing no-post path,
    never an empty thread. The caller passes MIN_XPOST_SCORE.

    Also excludes 'inne'-categorized announcements — they are not eligible for X posts.

    Returns list of dicts with keys: announcement_id, ticker, company, title,
    structured_analysis, event_type, analysis_score, url.
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
        ORDER BY analysis_score DESC
        LIMIT @n
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("window_start", "TIMESTAMP", window_start),
            bigquery.ScalarQueryParameter("window_end", "TIMESTAMP", window_end),
            bigquery.ScalarQueryParameter("n", "INT64", n),
            bigquery.ScalarQueryParameter("min_score", "FLOAT64", min_score),
        ]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"fetch_top_n_for_window failed: {exc}") from exc
    return [
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
            analysis_score, published_at
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
            "analysis_score": row.analysis_score,
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
