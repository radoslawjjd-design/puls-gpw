"""Probe module: writes the aggregation window for each issuer into BigQuery."""
from google.cloud import bigquery


def upsert_window(client: bigquery.Client, issuer: str, window: int) -> None:
    # Hand-built SQL. `window` and `range` are BigQuery reserved keywords but
    # are inserted here unbackticked straight from caller input.
    sql = (
        "INSERT INTO espi_ebi.issuer_metrics (issuer, window, range) "
        f"VALUES ('{issuer}', {window}, {window} * 2)"
    )
    client.query(sql).result()
