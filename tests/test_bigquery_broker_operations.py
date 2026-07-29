"""Tests for the broker-operations storage layer (PUL-95 xtb-portfolio-import).

The import stores raw broker operations as the source of truth; positions and
dividends are projections over them. Re-importing the same file must be a no-op,
which is what the insert-only MERGE keyed on external_id buys.

These are mocked-client tests: they pin the SQL that gets built, NOT that
BigQuery accepts it. Per the reserved-keyword lesson in
context/foundation/lessons.md, a real round-trip stays mandatory.
"""

from unittest.mock import MagicMock, patch

import pytest

from db.bigquery import (
    _USER_BROKER_OPERATIONS_SCHEMA,
    create_user_broker_operations_table_if_not_exists,
    delete_user_portfolio_positions,
    get_dividend_summary,
    merge_user_broker_operations,
    merge_user_portfolio_positions_bulk,
)
from src.exceptions import BigQueryError

# BigQuery reserved words that must never appear as a bare identifier in the SQL
# we build. `rows` is the one this change is most likely to reach for.
_RESERVED = ("rows", "range", "window", "groups", "hash", "partition")


def _mock_client_for_dml(affected_rows: int = 1) -> MagicMock:
    client = MagicMock()
    client.project = "test-project"
    job = MagicMock()
    job.result.return_value = None
    job.errors = None
    job.num_dml_affected_rows = affected_rows
    client.query.return_value = job
    return client


_POSITIONS = [
    {"ticker": "CBF", "company_name": "CyberFolks", "shares": 11.0, "avg_buy_price": 188.40},
    {"ticker": "TOA", "company_name": "Toya", "shares": 412.0, "avg_buy_price": 10.02},
]


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


_OPERATION_ROW = {
    "user_id": "u-1",
    "portfolio_id": "pf-1",
    "broker": "xtb",
    "external_id": "xtb:1364985363",
    "op_type": "buy",
    "occurred_at": "2026-07-21T09:51:44.265000+00:00",
    "imported_at": "2026-07-29T10:00:00+00:00",
    "amount_pln": -4420.0,
    "raw_type": "Stock purchase",
    "ticker": "VOT",
}


def test_schema_requires_identity_and_leaves_broker_specifics_nullable():
    modes = {field.name: field.mode for field in _USER_BROKER_OPERATIONS_SCHEMA}

    # Identity + the fields every broker must supply.
    for name in ("user_id", "portfolio_id", "broker", "external_id", "op_type",
                 "occurred_at", "imported_at", "amount_pln"):
        assert modes[name] == "REQUIRED", name
    # Anything a future broker might not carry stays optional.
    for name in ("raw_type", "ticker", "instrument_name", "volume", "unit_price",
                 "comment", "source_file"):
        assert modes[name] == "NULLABLE", name


def test_table_is_created_clustered_by_user_and_ticker():
    # Clustering cannot be migrated later — ensure_schema_current only appends
    # columns — so this is pinned at creation time.
    client = MagicMock()
    client.project = "test-project"
    client.get_table.side_effect = _not_found()

    with patch("db.bigquery._get_client", return_value=client):
        create_user_broker_operations_table_if_not_exists()

    created = client.create_table.call_args.args[0]
    assert created.clustering_fields == ["user_id", "ticker"]
    assert created.time_partitioning is None


def test_merge_dedupes_on_external_id_not_on_ticker_and_date():
    client = _mock_client_for_merge(affected_rows=571)

    with patch("db.bigquery._get_client", return_value=client):
        inserted = merge_user_broker_operations([_OPERATION_ROW])

    assert inserted == 571
    merge_sql = client.query.call_args[0][0]
    assert "PARTITION BY external_id" in merge_sql
    assert "ORDER BY imported_at DESC" in merge_sql
    assert "T.external_id = S.external_id" in merge_sql
    assert "snapshot_date" not in merge_sql


def test_merge_never_updates_an_existing_operation():
    # Re-importing the same file must not rewrite history: no WHEN MATCHED
    # branch at all, and QUALIFY so a duplicated source batch inserts once.
    client = _mock_client_for_merge()

    with patch("db.bigquery._get_client", return_value=client):
        merge_user_broker_operations([_OPERATION_ROW])

    merge_sql = client.query.call_args[0][0]
    assert "WHEN NOT MATCHED" in merge_sql
    assert "WHEN MATCHED" not in merge_sql.replace("WHEN NOT MATCHED", "")
    assert "QUALIFY ROW_NUMBER()" in merge_sql


def test_merge_of_an_empty_batch_touches_nothing():
    client = _mock_client_for_merge()

    with patch("db.bigquery._get_client", return_value=client):
        assert merge_user_broker_operations([]) == 0

    client.query.assert_not_called()
    client.load_table_from_json.assert_not_called()


def test_merge_failure_raises_and_cleans_up_the_temp_table():
    client = _mock_client_for_merge()
    client.query.side_effect = Exception("merge exploded")

    with patch("db.bigquery._get_client", return_value=client):
        with pytest.raises(BigQueryError, match="MERGE failed"):
            merge_user_broker_operations([_OPERATION_ROW])

    client.delete_table.assert_called_once()


def test_bulk_position_write_is_a_single_query_regardless_of_count():
    # The commit endpoint runs inside Cloud Run's 60s budget. Twenty sequential
    # upserts at 1-3s each would spend 20-60s just waiting on BigQuery.
    client = _mock_client_for_dml()

    with patch("db.bigquery._get_client", return_value=client):
        merge_user_portfolio_positions_bulk("u-1", "pf-1", _POSITIONS)

    assert client.query.call_count == 1
    assert "UNNEST" in client.query.call_args[0][0]


def test_bulk_position_write_never_deletes_rows_absent_from_the_file():
    # WHEN NOT MATCHED BY SOURCE would wipe exactly the holdings the export
    # structurally cannot contain — S2B and the spin-offs to come.
    client = _mock_client_for_dml()

    with patch("db.bigquery._get_client", return_value=client):
        merge_user_portfolio_positions_bulk("u-1", "pf-1", _POSITIONS)

    merge_sql = client.query.call_args[0][0]
    assert "BY SOURCE" not in merge_sql.upper()
    assert "WHEN MATCHED" in merge_sql
    assert "WHEN NOT MATCHED" in merge_sql


def test_bulk_position_write_avoids_reserved_words_as_identifiers():
    client = _mock_client_for_dml()

    with patch("db.bigquery._get_client", return_value=client):
        merge_user_portfolio_positions_bulk("u-1", "pf-1", _POSITIONS)

    merge_sql = client.query.call_args[0][0].lower()
    for word in _RESERVED:
        assert f"@{word}" not in merge_sql, word
        assert f" {word} " not in merge_sql, word


def test_bulk_position_write_keys_on_user_portfolio_and_ticker():
    client = _mock_client_for_dml()

    with patch("db.bigquery._get_client", return_value=client):
        merge_user_portfolio_positions_bulk("u-1", "pf-1", _POSITIONS)

    merge_sql = client.query.call_args[0][0]
    for column in ("user_id", "portfolio_id", "ticker"):
        assert f"T.{column} = S.{column}" in merge_sql


def test_bulk_position_write_of_nothing_issues_no_query():
    client = _mock_client_for_dml()

    with patch("db.bigquery._get_client", return_value=client):
        assert merge_user_portfolio_positions_bulk("u-1", "pf-1", []) == 0

    client.query.assert_not_called()


def test_closed_tickers_are_removed_in_one_statement():
    client = _mock_client_for_dml(affected_rows=2)

    with patch("db.bigquery._get_client", return_value=client):
        removed = delete_user_portfolio_positions("u-1", "pf-1", ["PZU", "MBR"])

    assert removed == 2
    assert client.query.call_count == 1
    sql = client.query.call_args[0][0]
    assert "DELETE" in sql and "IN UNNEST" in sql


def test_deleting_an_empty_ticker_list_issues_no_query():
    # A file with nothing closed must not emit a DELETE at all — an accidental
    # unfiltered statement here is the one non-reversible path in the import.
    client = _mock_client_for_dml()

    with patch("db.bigquery._get_client", return_value=client):
        assert delete_user_portfolio_positions("u-1", "pf-1", []) == 0

    client.query.assert_not_called()


def _mock_client_for_select(rows: list[dict]) -> MagicMock:
    client = MagicMock()
    client.project = "test-project"
    job = MagicMock()
    job.result.return_value = [_Row(r) for r in rows]
    client.query.return_value = job
    return client


class _Row(dict):
    """Stands in for a BigQuery Row: attribute and mapping access both work."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def test_dividend_summary_totals_split_gross_tax_and_net():
    # IKZE has zero withholding tax, so gross equals net there. Showing only one
    # number makes a correct account look broken — all three are returned.
    client = _mock_client_for_select([
        {"all_years": [2025, 2026], "year": 2026, "ticker": "KRU",
         "gross": 722.0, "tax": -137.18, "payouts": 2},
        {"all_years": [2025, 2026], "year": 2026, "ticker": "XTB",
         "gross": 444.0, "tax": -84.36, "payouts": 3},
    ])

    with patch("db.bigquery._get_client", return_value=client):
        summary = get_dividend_summary("u-1", "pf-1", 2026)

    assert summary["totals"]["gross"] == pytest.approx(1166.0)
    assert summary["totals"]["tax"] == pytest.approx(-221.54)
    assert summary["totals"]["net"] == pytest.approx(944.46)
    assert summary["totals"]["count"] == 5
    assert [b["ticker"] for b in summary["by_ticker"]] == ["KRU", "XTB"]


def test_dividend_summary_year_list_survives_an_empty_result():
    # PUL-100: the year list must come from a meta-first join, or switching to a
    # year with no payouts empties the selector and strands the user there.
    client = _mock_client_for_select([
        {"year": None, "ticker": None, "gross": None, "tax": None, "payouts": None,
         "all_years": [2025, 2026]},
    ])

    with patch("db.bigquery._get_client", return_value=client):
        summary = get_dividend_summary("u-1", "pf-1", 2024)

    assert summary["years"] == [2025, 2026]
    assert summary["by_ticker"] == []
    assert summary["totals"]["gross"] == pytest.approx(0.0)


def test_dividend_summary_sql_joins_meta_first_and_keeps_timestamp_precision():
    client = _mock_client_for_select([])

    with patch("db.bigquery._get_client", return_value=client):
        get_dividend_summary("u-1", None, None)

    sql = client.query.call_args[0][0]
    # meta LEFT JOIN data, never the other way round.
    assert "LEFT JOIN" in sql
    assert sql.index("meta") < sql.index("LEFT JOIN")
    # The year is derived for grouping; the stored timestamp is not truncated.
    assert "EXTRACT(YEAR FROM occurred_at)" in sql
    assert "TIMESTAMP_TRUNC" not in sql
    # Gross and tax are summed from separate op_types, never paired up.
    assert "dividend" in sql and "withholding_tax" in sql


def test_dividend_summary_across_all_portfolios_passes_no_portfolio_filter():
    client = _mock_client_for_select([])

    with patch("db.bigquery._get_client", return_value=client):
        get_dividend_summary("u-1", None, None)

    params = {p.name: p.value for p in client.query.call_args.kwargs["job_config"].query_parameters}
    assert params["user_id"] == "u-1"
    assert params.get("portfolio_id") is None


def _not_found():
    from google.cloud.exceptions import NotFound

    return NotFound("nope")
