"""Tests for insert-only MERGE write paths (PUL-92 backfill-historical-closes).

merge_company_daily_stats_insert_only / merge_etf_quotes_insert_only must never
overwrite existing (ticker, snapshot_date) rows — no WHEN MATCHED branch — and
must dedup the source batch inside the MERGE (QUALIFY), so a duplicated source
row can never insert twice.
"""

from unittest.mock import MagicMock, patch

import pytest

from db.bigquery import (
    merge_company_daily_stats_insert_only,
    merge_etf_quotes_insert_only,
)
from src.exceptions import BigQueryError


def _mock_client_for_merge(affected_rows: int = 1) -> MagicMock:
    client = MagicMock()
    client.project = "test-project"
    load_job = MagicMock()
    load_job.result.return_value = None
    load_job.errors = None
    client.load_table_from_json.return_value = load_job
    merge_job = MagicMock()
    merge_job.result.return_value = None
    merge_job.errors = None
    merge_job.num_dml_affected_rows = affected_rows
    client.query.return_value = merge_job
    return client


_COMPANY_ROW = {
    "ticker": "KRU",
    "snapshot_date": "2026-01-02",
    "kurs_zamkniecia": 498.4,
    "fetched_at": "2026-07-24T12:00:00+00:00",
}

_ETF_ROW = {
    "ticker": "ETFBW20TR",
    "snapshot_date": "2026-01-05",
    "kurs_zamkniecia": 64.84,
    "fetched_at": "2026-07-24T12:00:00+00:00",
}


def test_merge_company_daily_stats_insert_only_never_updates():
    """SQL must target company_daily_stats with WHEN NOT MATCHED only, dedup the
    source via QUALIFY, clean up the temp table, and return the inserted count."""
    client = _mock_client_for_merge(affected_rows=7)

    with patch("db.bigquery._get_client", return_value=client):
        inserted = merge_company_daily_stats_insert_only([_COMPANY_ROW])

    assert inserted == 7
    client.load_table_from_json.assert_called_once()
    merge_sql = client.query.call_args[0][0]
    assert "MERGE" in merge_sql and "company_daily_stats" in merge_sql
    assert "WHEN NOT MATCHED" in merge_sql
    assert "WHEN MATCHED" not in merge_sql.replace("WHEN NOT MATCHED", "")
    assert "QUALIFY ROW_NUMBER()" in merge_sql
    assert "PARTITION BY ticker, snapshot_date" in merge_sql
    client.delete_table.assert_called_once()
    assert "_tmp_" in client.delete_table.call_args.args[0]


def test_merge_etf_quotes_insert_only_never_updates():
    """Same contract for etf_quotes, including ETF-specific columns in the INSERT."""
    client = _mock_client_for_merge(affected_rows=3)

    with patch("db.bigquery._get_client", return_value=client):
        inserted = merge_etf_quotes_insert_only([_ETF_ROW])

    assert inserted == 3
    merge_sql = client.query.call_args[0][0]
    assert "MERGE" in merge_sql and "etf_quotes" in merge_sql
    assert "WHEN NOT MATCHED" in merge_sql
    assert "WHEN MATCHED" not in merge_sql.replace("WHEN NOT MATCHED", "")
    assert "QUALIFY ROW_NUMBER()" in merge_sql
    assert "kurs_odn" in merge_sql and "wolumen_skum" in merge_sql
    client.delete_table.assert_called_once()


@pytest.mark.parametrize(
    "func", [merge_company_daily_stats_insert_only, merge_etf_quotes_insert_only]
)
def test_insert_only_empty_rows_is_noop(func):
    """Empty rows must return 0 without any BQ calls."""
    client = _mock_client_for_merge()

    with patch("db.bigquery._get_client", return_value=client):
        assert func([]) == 0

    client.load_table_from_json.assert_not_called()
    client.query.assert_not_called()
    client.delete_table.assert_not_called()


def test_insert_only_load_failure_raises_and_cleans_up():
    """A failed load job must raise BigQueryError and still delete the temp table."""
    client = _mock_client_for_merge()
    client.load_table_from_json.return_value.errors = [{"reason": "invalid"}]

    with patch("db.bigquery._get_client", return_value=client):
        with pytest.raises(BigQueryError, match="load failed"):
            merge_company_daily_stats_insert_only([_COMPANY_ROW])

    client.delete_table.assert_called_once()
    assert client.delete_table.call_args.kwargs.get("not_found_ok") is True


def test_insert_only_merge_failure_raises_and_cleans_up():
    """A failed MERGE query must raise BigQueryError and still delete the temp table."""
    client = _mock_client_for_merge()
    client.query.side_effect = Exception("merge exploded")

    with patch("db.bigquery._get_client", return_value=client):
        with pytest.raises(BigQueryError, match="MERGE failed"):
            merge_etf_quotes_insert_only([_ETF_ROW])

    client.delete_table.assert_called_once()
