"""
BigQuery client — dedup + zapis ogłoszeń.

Tabela: {BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}
Partycja: announcement_date
Cluster:  company

Dedup: hash(bankier_url) jako id — Bankier URL jest unikalny per ogłoszenie.
Tabela tworzona automatycznie przy pierwszym uruchomieniu (create_if_missing).
"""
import hashlib
import logging
from datetime import date, datetime, time

from google.cloud import bigquery

from config import BQ_DATASET, BQ_PROJECT, BQ_TABLE

logger = logging.getLogger(__name__)

_TABLE_ID = f"{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"

_SCHEMA = [
    bigquery.SchemaField("id",                "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("title",             "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("company",           "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("announcement_date", "DATE",      mode="REQUIRED"),
    bigquery.SchemaField("pub_time",          "TIME",      mode="NULLABLE"),
    bigquery.SchemaField("bankier_url",       "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("espi_url",          "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("content_text",      "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("xpost",             "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("supervisor_score",  "INTEGER",   mode="NULLABLE"),
    bigquery.SchemaField("email_sent",        "BOOL",      mode="NULLABLE"),
    bigquery.SchemaField("scraped_at",        "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("source",            "STRING",    mode="REQUIRED"),
]

_client: bigquery.Client | None = None


def _get_client() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client(project=BQ_PROJECT)
    return _client


def announcement_id(ann: dict) -> str:
    """MD5[:16] Bankier URL jako stabilny dedup key."""
    key = ann.get("bankier_url") or ann.get("url") or f"{ann['company']}|{ann['title']}|{ann['date']}"
    return hashlib.md5(key.encode()).hexdigest()[:16]


def ensure_table_exists() -> None:
    client  = _get_client()
    table   = bigquery.Table(_TABLE_ID, schema=_SCHEMA)
    table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field="announcement_date",
    )
    table.clustering_fields = ["company"]
    try:
        client.get_table(_TABLE_ID)
    except Exception:
        client.create_table(table)
        logger.info(f"Tabela BigQuery utworzona: {_TABLE_ID}")


def is_duplicate(ann_id: str) -> bool:
    """Sprawdza czy ogłoszenie o danym id jest już w BigQuery."""
    client = _get_client()
    query  = f"SELECT 1 FROM `{_TABLE_ID}` WHERE id = @id LIMIT 1"
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", ann_id)]
    )
    try:
        result = client.query(query, job_config=job_config).result()
        return bool(list(result))
    except Exception as e:
        logger.error(f"BQ is_duplicate błąd: {e}")
        # Konserwatywnie: jeśli nie możemy sprawdzić, traktujemy jako nowe
        return False


def save_announcement(
    ann:            dict,
    content_text:   str,
    xpost:          str | None,
    supervisor_score: int | None,
    email_sent:     bool = False,
) -> None:
    client = _get_client()
    ann_id = announcement_id(ann)

    ann_date = ann["date"]
    if isinstance(ann_date, datetime):
        ann_date = ann_date.date()

    pub_time = ann.get("pub_time")
    if isinstance(pub_time, time):
        pub_time = pub_time.strftime("%H:%M:%S")
    elif pub_time is None:
        pub_time = None

    row = {
        "id":                ann_id,
        "title":             ann["title"][:1000],
        "company":           ann["company"],
        "announcement_date": ann_date.isoformat(),
        "pub_time":          pub_time,
        "bankier_url":       ann.get("bankier_url"),
        "espi_url":          ann.get("url"),
        "content_text":      content_text[:50_000] if content_text else None,
        "xpost":             xpost,
        "supervisor_score":  supervisor_score,
        "email_sent":        email_sent,
        "scraped_at":        datetime.utcnow().isoformat(),
        "source":            ann.get("source", "bankier"),
    }

    errors = client.insert_rows_json(_TABLE_ID, [row])
    if errors:
        logger.error(f"BQ insert błąd dla {ann['company']}: {errors}")
    else:
        logger.info(f"BQ zapisano: {ann['company']} — {ann['title'][:60]}")
