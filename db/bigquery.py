"""BigQuery client, schema definition, and CRUD wrappers for the announcements table."""
import hashlib
import logging
import os
import threading
from datetime import datetime

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
    bigquery.SchemaField("processed_at", "TIMESTAMP", mode="NULLABLE"),
    bigquery.SchemaField("supervisor_attempts", "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("analysis_type", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("parsed_content", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("priority", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("structured_analysis", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("analysis_approved", "BOOL", mode="NULLABLE"),
    bigquery.SchemaField("analysis_reject_reason", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("event_type", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("analysis_score", "FLOAT64", mode="NULLABLE"),
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


def _table_ref(client: bigquery.Client) -> str:
    return f"{client.project}.{_DATASET}.{_TABLE_NAME}"


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


def ensure_schema_current() -> None:
    """Add any missing columns from _SCHEMA to the existing BQ table.

    Safe to call on every startup — no-op if schema is already current.
    Raises BigQueryError if the schema update fails.
    """
    client = _get_client()
    table_id = _table_ref(client)
    try:
        table = client.get_table(table_id)
    except NotFound:
        logger.info("BQ table not found — run create_table_if_not_exists() first")
        return
    existing_names = {f.name for f in table.schema}
    missing = [f for f in _SCHEMA if f.name not in existing_names]
    if not missing:
        logger.info("BQ schema already current")
        return
    table.schema = table.schema + missing
    try:
        client.update_table(table, ["schema"])
        logger.info("BQ schema updated: added columns %s", [f.name for f in missing])
    except Exception as exc:
        raise BigQueryError(f"ensure_schema_current failed: {exc}") from exc


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
    company: str | None,
    ticker: str | None,
    priority: str | None = None,
) -> str:
    """Insert a new announcement row and return its announcement_id.

    Uses DML INSERT (not streaming) so subsequent UPDATE/DELETE in the same
    session are not blocked by the streaming buffer.
    Raises RuntimeError if the query job fails.
    """
    client = _get_client()
    ann_id = _announcement_id(url)
    query = f"""
        INSERT INTO `{_table_ref(client)}`
            (announcement_id, url, published_at, title, company, ticker,
             post_text, processed_at, supervisor_attempts, analysis_type, priority)
        VALUES
            (@id, @url, @published_at, @title, @company, @ticker,
             NULL, NULL, NULL, NULL, @priority)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("id", "STRING", ann_id),
            bigquery.ScalarQueryParameter("url", "STRING", url),
            bigquery.ScalarQueryParameter("published_at", "TIMESTAMP", published_at),
            bigquery.ScalarQueryParameter("title", "STRING", title),
            bigquery.ScalarQueryParameter("company", "STRING", company),
            bigquery.ScalarQueryParameter("ticker", "STRING", ticker),
            bigquery.ScalarQueryParameter("priority", "STRING", priority),
        ]
    )
    job = client.query(query, job_config=job_config)
    result = job.result()
    if job.errors:
        raise BigQueryError(f"insert_announcement failed: {job.errors}")
    logger.debug("Inserted announcement_id=%s", ann_id)
    return ann_id


def save_analysis(
    announcement_id: str,
    post_text: str,
    analysis_type: str,
    supervisor_attempts: int,
) -> None:
    """Update a row with analysis results. analysis_type must be FINANCIAL or CORPORATE."""
    if analysis_type not in ("FINANCIAL", "CORPORATE"):
        raise ValueError(f"analysis_type must be FINANCIAL or CORPORATE, got: {analysis_type!r}")
    client = _get_client()
    query = f"""
        UPDATE `{_table_ref(client)}`
        SET
            post_text = @post_text,
            analysis_type = @analysis_type,
            supervisor_attempts = @supervisor_attempts,
            processed_at = CURRENT_TIMESTAMP()
        WHERE announcement_id = @id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("post_text", "STRING", post_text),
            bigquery.ScalarQueryParameter("analysis_type", "STRING", analysis_type),
            bigquery.ScalarQueryParameter("supervisor_attempts", "INTEGER", supervisor_attempts),
            bigquery.ScalarQueryParameter("id", "STRING", announcement_id),
        ]
    )
    job = client.query(query, job_config=job_config)
    job.result()
    if job.errors:
        raise BigQueryError(f"save_analysis failed: {job.errors}")
    if job.num_dml_affected_rows == 0:
        raise BigQueryError(f"save_analysis: no row matched announcement_id={announcement_id!r}")


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
            analysis_score = @analysis_score
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
) -> list[dict]:
    """Return top-N approved announcements for a time window, ordered by score DESC.

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
          AND published_at BETWEEN @window_start AND @window_end
        ORDER BY analysis_score DESC
        LIMIT @n
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("window_start", "TIMESTAMP", window_start),
            bigquery.ScalarQueryParameter("window_end", "TIMESTAMP", window_end),
            bigquery.ScalarQueryParameter("n", "INT64", n),
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


def save_post_text(
    announcement_ids: list[str],
    post_text: str | None,
    supervisor_attempts: int,
) -> None:
    """Batch-update post_text, processed_at, supervisor_attempts for all contributing rows.

    post_text=None records a failed generation attempt (BQ stores NULL).
    Raises BigQueryError on failure.
    """
    client = _get_client()
    query = f"""
        UPDATE `{_table_ref(client)}`
        SET
            post_text = @post_text,
            supervisor_attempts = @supervisor_attempts,
            processed_at = CURRENT_TIMESTAMP()
        WHERE announcement_id IN UNNEST(@ids)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("post_text", "STRING", post_text),
            bigquery.ScalarQueryParameter("supervisor_attempts", "INTEGER", supervisor_attempts),
            bigquery.ArrayQueryParameter("ids", "STRING", announcement_ids),
        ]
    )
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"save_post_text failed: {exc}") from exc
    if job.errors:
        raise BigQueryError(f"save_post_text failed: {job.errors}")
    if job.num_dml_affected_rows == 0:
        raise BigQueryError(f"save_post_text: 0 rows updated for ids={announcement_ids!r}")
    logger.debug("save_post_text: updated %d rows, attempts=%d", len(announcement_ids), supervisor_attempts)


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
    Raises RuntimeError if the BQ query fails.
    """
    client = _get_client()
    query = f"SELECT announcement_id FROM `{_table_ref(client)}` WHERE published_at >= @cutoff"
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("cutoff", "TIMESTAMP", cutoff)]
    )
    rows = list(client.query(query, job_config=job_config).result())
    return {row.announcement_id for row in rows}
