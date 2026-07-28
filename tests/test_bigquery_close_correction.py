"""Tests for the narrow close-correction MERGE (PUL-98 official-close-source).

merge_company_daily_stats_close_correction repairs a close that was written from
the wrong source. Two properties make it safe to run over history:

* it updates a deliberately narrow column set — turnover, trade count, the OHLC
  levels and fetched_at belong to the session that was actually observed, and a
  correction has no better knowledge of them;
* it has no WHEN NOT MATCHED branch, so a date the table never carried is never
  introduced. The trading-day spine is `SELECT DISTINCT snapshot_date` from this
  table, and a phantom row would silently redefine what a session day is.
"""

from unittest.mock import MagicMock, patch

import pytest

from db.bigquery import merge_company_daily_stats_close_correction
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


_CORRECTION_ROW = {
    "ticker": "ALE",
    "snapshot_date": "2026-07-24",
    "kurs_zamkniecia": 44.735,
    "zmiana_procentowa": -1.02,
    "zmiana_kwotowa": -0.46,
    "source": "archive",
    "fetched_at": "2026-07-28T09:00:00+00:00",
}


def _merge_sql(rows=(_CORRECTION_ROW,), affected_rows=1):
    client = _mock_client_for_merge(affected_rows=affected_rows)
    with patch("db.bigquery._get_client", return_value=client):
        returned = merge_company_daily_stats_close_correction(list(rows))
    return client, client.query.call_args[0][0], returned


def test_close_correction_updates_only_the_close_and_its_derived_columns():
    """The UPDATE SET carries exactly four columns and nothing else."""
    client, sql, returned = _merge_sql(affected_rows=249)

    assert returned == 249
    assert "MERGE" in sql and "company_daily_stats" in sql
    assert "T.ticker = S.ticker AND T.snapshot_date = S.snapshot_date" in sql
    assert "WHEN MATCHED" in sql
    for column in ("kurs_zamkniecia", "zmiana_procentowa", "zmiana_kwotowa", "source"):
        assert f"{column} = S.{column}" in sql
    client.delete_table.assert_called_once()
    assert "_tmp_" in client.delete_table.call_args.args[0]


@pytest.mark.parametrize(
    "column",
    ["kurs_otwarcia", "kurs_min", "kurs_max", "wartosc_obrotu", "liczba_transakcji",
     "fetched_at", "kurs_odn"],
)
def test_close_correction_leaves_the_observed_session_columns_alone(column):
    """A correction knows the close, not the session that produced it."""
    _, sql, _ = _merge_sql()

    assert f"{column} = S.{column}" not in sql


def test_close_correction_never_inserts_a_missing_date():
    """No WHEN NOT MATCHED branch — the trading-day spine stays as observed."""
    _, sql, _ = _merge_sql()

    assert "WHEN NOT MATCHED" not in sql
    assert "INSERT" not in sql
    assert "QUALIFY ROW_NUMBER()" in sql
    assert "PARTITION BY ticker, snapshot_date" in sql


def test_close_correction_empty_rows_is_a_noop():
    client = _mock_client_for_merge()

    with patch("db.bigquery._get_client", return_value=client):
        assert merge_company_daily_stats_close_correction([]) == 0

    client.load_table_from_json.assert_not_called()
    client.query.assert_not_called()
    client.delete_table.assert_not_called()


def test_close_correction_none_affected_rows_returns_zero():
    """A MERGE that matched nothing reports None, not 0 — map it, don't crash."""
    client = _mock_client_for_merge()
    client.query.return_value.num_dml_affected_rows = None

    with patch("db.bigquery._get_client", return_value=client):
        assert merge_company_daily_stats_close_correction([_CORRECTION_ROW]) == 0


@pytest.mark.parametrize(
    ("break_client", "expected"),
    [
        (lambda c: setattr(c.load_table_from_json.return_value, "errors", [{"r": "x"}]),
         "load failed"),
        (lambda c: setattr(c, "query", MagicMock(side_effect=Exception("boom"))),
         "MERGE failed"),
    ],
)
def test_close_correction_failures_raise_and_still_clean_up(break_client, expected):
    client = _mock_client_for_merge()
    break_client(client)

    with patch("db.bigquery._get_client", return_value=client):
        with pytest.raises(BigQueryError, match=expected):
            merge_company_daily_stats_close_correction([_CORRECTION_ROW])

    client.delete_table.assert_called_once()
    assert client.delete_table.call_args.kwargs.get("not_found_ok") is True
