"""Migrate the announcements table to a DATE-partitioned + clustered layout.

Re-creates the announcements table with:
  PARTITION BY DATE(published_at)   -- enables partition pruning on date filters
  CLUSTER BY ticker                 -- speeds up per-ticker queries

Sequence (--execute):
  1. CREATE TABLE IF NOT EXISTS {dataset}.announcements_backup
        AS SELECT * FROM {dataset}.announcements
  2. CREATE OR REPLACE TABLE {dataset}.announcements
        PARTITION BY <clause> CLUSTER BY ticker
        AS SELECT * FROM {dataset}.announcements_backup

--dry-run  print SQL only, do not execute
--execute  run against BigQuery (human-only, destructive)

Run with:
    uv run python scripts/migrate_announcements_partition.py --dry-run
    uv run python scripts/migrate_announcements_partition.py --execute
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.logging_setup import configure_logging
configure_logging()
logger = logging.getLogger(__name__)

from db.bigquery import _get_client, _DATASET, _TABLE_NAME


def _detect_partition_clause(client) -> str:
    from google.cloud import bigquery as bq
    table_ref = f"{client.project}.{_DATASET}.{_TABLE_NAME}"
    schema = client.get_table(table_ref).schema
    field = next((f for f in schema if f.name == "published_at"), None)
    if field is None:
        raise RuntimeError("Column published_at not found in announcements schema")
    if field.field_type == "TIMESTAMP":
        return "DATE(published_at)"
    return "published_at"


def build_sql(client) -> list[str]:
    dataset = f"{client.project}.{_DATASET}"
    partition_clause = _detect_partition_clause(client)
    backup_sql = (
        f"CREATE TABLE IF NOT EXISTS `{dataset}.announcements_backup`\n"
        f"AS SELECT * FROM `{dataset}.{_TABLE_NAME}`"
    )
    recreate_sql = (
        f"CREATE OR REPLACE TABLE `{dataset}.{_TABLE_NAME}`\n"
        f"PARTITION BY {partition_clause}\n"
        f"CLUSTER BY ticker\n"
        f"AS SELECT * FROM `{dataset}.announcements_backup`"
    )
    return [backup_sql, recreate_sql]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Print SQL only, do not execute")
    group.add_argument("--execute", action="store_true", help="Execute against BigQuery (destructive)")
    args = parser.parse_args()

    client = _get_client()
    statements = build_sql(client)

    if args.dry_run:
        print("-- DRY RUN — no changes will be made --\n")
        for i, sql in enumerate(statements, 1):
            print(f"-- Step {i} --")
            print(sql)
            print()
        return

    logger.info("Starting announcements partition migration (--execute)")
    for i, sql in enumerate(statements, 1):
        logger.info("Step %d/%d:\n%s", i, len(statements), sql)
        job = client.query(sql)
        job.result()
        if job.errors:
            raise RuntimeError(f"Migration step {i} failed: {job.errors}")
        logger.info("Step %d done", i)

    logger.info("Migration complete. Verify in BQ Console: partition=published_at, cluster=ticker")


if __name__ == "__main__":
    main()
