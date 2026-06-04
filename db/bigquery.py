"""BigQuery client, schema definition, and CRUD wrappers for the announcements table."""
import hashlib
import os
import threading
from datetime import datetime
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

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
                _client = bigquery.Client(project=project, credentials=credentials)
    return _client


def _table_ref(client: bigquery.Client) -> str:
    return f"{client.project}.{_DATASET}.{_TABLE_NAME}"


def _announcement_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def create_table_if_not_exists() -> None:
    """Create the announcements table in BigQuery if it does not already exist."""
    client = _get_client()
    table_id = _table_ref(client)
    try:
        client.get_table(table_id)
    except NotFound:
        table = bigquery.Table(table_id, schema=_SCHEMA)
        client.create_table(table)


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
             post_text, processed_at, supervisor_attempts, analysis_type)
        VALUES
            (@id, @url, @published_at, @title, @company, @ticker,
             NULL, NULL, NULL, NULL)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("id", "STRING", ann_id),
            bigquery.ScalarQueryParameter("url", "STRING", url),
            bigquery.ScalarQueryParameter("published_at", "TIMESTAMP", published_at),
            bigquery.ScalarQueryParameter("title", "STRING", title),
            bigquery.ScalarQueryParameter("company", "STRING", company),
            bigquery.ScalarQueryParameter("ticker", "STRING", ticker),
        ]
    )
    job = client.query(query, job_config=job_config)
    result = job.result()
    if job.errors:
        raise RuntimeError(f"insert_announcement failed: {job.errors}")
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
        raise RuntimeError(f"save_analysis failed: {job.errors}")
    if job.num_dml_affected_rows == 0:
        raise RuntimeError(f"save_analysis: no row matched announcement_id={announcement_id!r}")
