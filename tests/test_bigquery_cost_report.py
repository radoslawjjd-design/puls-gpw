"""Tests for the billing-export read layer (PUL-125 daily-cost-report).

These are mocked-client tests: they pin the SQL that gets built, NOT that
BigQuery accepts it. Per the reserved-keyword lesson in
context/foundation/lessons.md, the real round-trip
(`scripts/test_bq_billing_export.py`) stays mandatory.

Two things here are unlike every other query in this repo and are the reason
the SQL is asserted at all:

* `credits` is a REPEATED RECORD on the table, so netting it out needs an
  UNNEST over a *table column* — the first in this codebase. Every other
  UNNEST unnests a query parameter.
* `usage_start_time` is a UTC instant while the report is a Warsaw-day
  report, so the day bucket must name the zone inside the SQL. Without it a
  cost incurred at 00:30 Warsaw lands in the previous day (PUL-120 bit us
  the same way on dividends).
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from db.bigquery import get_billing_rows, get_daily_gross
from src.exceptions import BigQueryError


def _mock_client_with_rows(rows: list[dict]) -> MagicMock:
    client = MagicMock()
    client.project = "test-project"
    mock_rows = []
    for row_dict in rows:
        row = MagicMock()
        for k, v in row_dict.items():
            setattr(row, k, v)
        mock_rows.append(row)
    job = MagicMock()
    job.result.return_value = mock_rows
    job.errors = None
    client.query.return_value = job
    return client


def _sql(client: MagicMock) -> str:
    """The built query, whitespace-normalized so formatting can't break asserts."""
    return " ".join(client.query.call_args[0][0].split())


def _params(client: MagicMock) -> dict:
    return {p.name: p for p in client.query.call_args.kwargs["job_config"].query_parameters}


def test_billing_rows_bucket_the_day_in_warsaw_not_utc():
    """usage_start_time is a UTC instant; the report is a Warsaw-day report."""
    client = _mock_client_with_rows([])

    with patch("db.bigquery._get_client", return_value=client):
        get_billing_rows(date(2026, 8, 1), date(2026, 8, 6))

    assert "'Europe/Warsaw'" in _sql(client)


def test_billing_rows_net_out_credits_via_unnest():
    """Net is gross plus the (negative) credit amounts, which live in a REPEATED RECORD."""
    client = _mock_client_with_rows([])

    with patch("db.bigquery._get_client", return_value=client):
        get_billing_rows(date(2026, 8, 1), date(2026, 8, 6))

    sql = _sql(client)
    assert "UNNEST(credits)" in sql


def test_billing_rows_bind_the_date_bounds_as_parameters():
    """Bounds are bound, never interpolated — the house rule for every value in SQL."""
    client = _mock_client_with_rows([])

    with patch("db.bigquery._get_client", return_value=client):
        get_billing_rows(date(2026, 8, 1), date(2026, 8, 6))

    params = _params(client)
    assert params["start"].type_ == "DATE"
    assert params["end"].type_ == "DATE"
    assert params["start"].value == date(2026, 8, 1)
    assert params["end"].value == date(2026, 8, 6)


def test_billing_rows_carry_the_columns_the_report_needs():
    """One row per (day, service, sku) with cost, credit-adjusted cost and usage."""
    client = _mock_client_with_rows(
        [
            {
                "day": date(2026, 8, 5),
                "service": "Vertex AI",
                "sku": "Gemini 2.5 Flash GA Text Input - Predictions",
                "gross": 1.2143,
                "net": -0.0001,
                "usage_amount": 1065040.0,
                "usage_unit": "requests",
            }
        ]
    )

    with patch("db.bigquery._get_client", return_value=client):
        rows = get_billing_rows(date(2026, 8, 5), date(2026, 8, 5))

    assert rows == [
        {
            "day": date(2026, 8, 5),
            "service": "Vertex AI",
            "sku": "Gemini 2.5 Flash GA Text Input - Predictions",
            "gross": 1.2143,
            "net": -0.0001,
            "usage_amount": 1065040.0,
            "usage_unit": "requests",
        }
    ]


def test_billing_rows_wrap_query_failure_as_bigquery_error():
    client = MagicMock()
    client.project = "test-project"
    client.query.side_effect = RuntimeError("boom")

    with patch("db.bigquery._get_client", return_value=client):
        with pytest.raises(BigQueryError, match="get_billing_rows failed"):
            get_billing_rows(date(2026, 8, 1), date(2026, 8, 6))


def test_daily_gross_returns_one_total_per_day():
    """The anomaly baseline needs day totals only — no service or SKU dimension."""
    client = _mock_client_with_rows(
        [
            {"day": date(2026, 8, 4), "gross": 1.278},
            {"day": date(2026, 8, 5), "gross": 2.778},
        ]
    )

    with patch("db.bigquery._get_client", return_value=client):
        totals = get_daily_gross(date(2026, 8, 4), date(2026, 8, 5))

    assert totals == {date(2026, 8, 4): 1.278, date(2026, 8, 5): 2.778}


def test_daily_gross_buckets_the_day_in_warsaw_too():
    """Same instant-vs-day trap as get_billing_rows; the baseline must agree with the report."""
    client = _mock_client_with_rows([])

    with patch("db.bigquery._get_client", return_value=client):
        get_daily_gross(date(2026, 7, 30), date(2026, 8, 5))

    assert "'Europe/Warsaw'" in _sql(client)


def test_daily_gross_wraps_query_failure_as_bigquery_error():
    client = MagicMock()
    client.project = "test-project"
    client.query.side_effect = RuntimeError("boom")

    with patch("db.bigquery._get_client", return_value=client):
        with pytest.raises(BigQueryError, match="get_daily_gross failed"):
            get_daily_gross(date(2026, 8, 1), date(2026, 8, 6))
