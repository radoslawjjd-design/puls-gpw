"""Tests for ETF/ETC/ETN BigQuery layer (PUL-67)."""
from unittest.mock import MagicMock, patch

import pytest

# ── Phase 4: list_user_portfolio_positions COALESCE ──────────────────────────

def test_list_user_portfolio_positions_query_includes_etf_quotes_coalesce():
    """list_user_portfolio_positions must COALESCE company_daily_stats and etf_quotes for current_price."""
    from db.bigquery import list_user_portfolio_positions

    mock = MagicMock()
    mock.project = "test-project"
    job = MagicMock()
    job.result.return_value = []
    mock.query.return_value = job

    with patch("db.bigquery._get_client", return_value=mock):
        list_user_portfolio_positions("user-1")

    query_str = mock.query.call_args[0][0]
    assert "etf_quotes" in query_str, "Query must JOIN etf_quotes table"
    assert "COALESCE" in query_str, "Query must COALESCE company_daily_stats and etf_quotes prices"


class _FakeBQRow(dict):
    """Minimal BQ Row fake: dict() works, attribute access works."""
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


def test_list_user_portfolio_positions_etf_price_falls_back_from_etf_quotes():
    """When BQ returns current_price from etf_quotes (company_daily_stats has no row), result includes it."""
    from db.bigquery import list_user_portfolio_positions

    row = _FakeBQRow(
        portfolio_id="port-1",
        ticker="ETFBW20TR",
        company_name="ETFBW20TR",
        shares=10.0,
        avg_buy_price=70.0,
        current_price=72.81,   # came from etf_quotes via COALESCE
        daily_change_pct=-0.25,
        price_as_of="2026-06-29",
    )
    mock = MagicMock()
    mock.project = "test-project"
    job = MagicMock()
    job.result.return_value = [row]
    mock.query.return_value = job

    with patch("db.bigquery._get_client", return_value=mock):
        result = list_user_portfolio_positions("user-1")

    assert len(result) == 1
    assert result[0]["ticker"] == "ETFBW20TR"
    assert result[0]["current_price"] == pytest.approx(72.81)


def _mock_bq_client_for_merge() -> MagicMock:
    client = MagicMock()
    client.project = "test-project"
    load_job = MagicMock()
    load_job.result.return_value = None
    load_job.errors = None
    client.load_table_from_json.return_value = load_job
    merge_job = MagicMock()
    merge_job.result.return_value = None
    merge_job.errors = None
    client.query.return_value = merge_job
    return client


def _mock_bq_client_with_rows(rows: list[dict]) -> MagicMock:
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


# ── Phase 5: get_portfolio_calendar_data COALESCE ────────────────────────────

def test_get_portfolio_calendar_data_query_includes_etf_quotes_coalesce():
    """get_portfolio_calendar_data must JOIN etf_quotes and COALESCE prices for ETF tickers."""
    from db.bigquery import get_portfolio_calendar_data

    mock = MagicMock()
    mock.project = "test-project"
    job = MagicMock()
    job.result.return_value = []
    mock.query.return_value = job

    with patch("db.bigquery._get_client", return_value=mock):
        get_portfolio_calendar_data("port-1", "user-1", 2026, 6)

    query_str = mock.query.call_args[0][0]
    assert "etf_quotes" in query_str, "Calendar query must JOIN etf_quotes"
    assert "COALESCE" in query_str, "Calendar query must COALESCE company_daily_stats and etf_quotes"


# ── Phase 1.A: create_etf_quotes_table_if_not_exists ─────────────────────────

def test_create_etf_quotes_table_creates_with_partitioning_and_clustering():
    """create_etf_quotes_table_if_not_exists must create with snapshot_date partition + ticker cluster."""
    from google.cloud.exceptions import NotFound
    from db.bigquery import create_etf_quotes_table_if_not_exists

    client = MagicMock()
    client.project = "test-project"
    client.get_table.side_effect = NotFound("missing")

    with patch("db.bigquery._get_client", return_value=client):
        create_etf_quotes_table_if_not_exists()

    assert client.create_table.called
    created_table = client.create_table.call_args[0][0]
    assert "etf_quotes" in str(created_table.reference) or "etf_quotes" in str(created_table)
    assert created_table.time_partitioning is not None
    assert created_table.time_partitioning.field == "snapshot_date"
    assert created_table.clustering_fields == ["ticker"]


def test_create_etf_quotes_table_no_op_if_exists():
    """create_etf_quotes_table_if_not_exists must not call create_table when table exists."""
    from db.bigquery import create_etf_quotes_table_if_not_exists

    client = MagicMock()
    client.project = "test-project"
    client.get_table.return_value = MagicMock()

    with patch("db.bigquery._get_client", return_value=client):
        create_etf_quotes_table_if_not_exists()

    assert not client.create_table.called


# ── Phase 1.B: merge_etf_quotes ──────────────────────────────────────────────

def test_merge_etf_quotes_happy_path():
    """merge_etf_quotes must call load_table_from_json, MERGE on (ticker, snapshot_date), delete_table."""
    from db.bigquery import merge_etf_quotes

    client = _mock_bq_client_for_merge()
    rows = [{"ticker": "ETFBW20TR", "snapshot_date": "2026-06-29",
             "kurs_zamkniecia": 72.81, "zmiana_procentowa": -0.25,
             "zmiana_kwotowa": -0.18, "kurs_odn": 72.99,
             "fetched_at": "2026-06-29T10:00:00+00:00"}]

    with patch("db.bigquery._get_client", return_value=client):
        merge_etf_quotes(rows)

    client.load_table_from_json.assert_called_once()
    client.query.assert_called_once()
    merge_sql = client.query.call_args[0][0]
    assert "MERGE" in merge_sql
    assert "etf_quotes" in merge_sql
    assert "snapshot_date" in merge_sql
    client.delete_table.assert_called_once()


def test_merge_etf_quotes_empty_rows_is_noop():
    """merge_etf_quotes must return immediately without any BQ calls when rows is empty."""
    from db.bigquery import merge_etf_quotes

    client = MagicMock()
    client.project = "test-project"

    with patch("db.bigquery._get_client", return_value=client):
        merge_etf_quotes([])

    client.load_table_from_json.assert_not_called()
    client.query.assert_not_called()


# ── Phase 1.C: merge_etf_instruments ─────────────────────────────────────────

def test_merge_etf_instruments_happy_path():
    """merge_etf_instruments must MERGE on ticker only (no snapshot_date)."""
    from db.bigquery import merge_etf_instruments

    client = _mock_bq_client_for_merge()
    rows = [{"ticker": "ETFBW20TR", "name": "ETFBW20TR", "isin": "PLBTETF00015",
             "instrument_type": "ETF", "created_at": "2026-06-29T10:00:00+00:00",
             "updated_at": "2026-06-29T10:00:00+00:00"}]

    with patch("db.bigquery._get_client", return_value=client):
        merge_etf_instruments(rows)

    client.load_table_from_json.assert_called_once()
    merge_sql = client.query.call_args[0][0]
    assert "MERGE" in merge_sql
    assert "etf_instruments" in merge_sql
    client.delete_table.assert_called_once()


# ── Phase 1.D: list_distinct_tickers UNION ───────────────────────────────────

def test_list_distinct_tickers_includes_etf_instruments_via_union():
    """list_distinct_tickers must UNION companies + etf_instruments so ETF tickers appear."""
    from db.bigquery import list_distinct_tickers

    rows = [{"ticker": "CDR"}, {"ticker": "ETFBW20TR"}, {"ticker": "PKO"}]
    mock = _mock_bq_client_with_rows(rows)

    with patch("db.bigquery._get_client", return_value=mock):
        result = list_distinct_tickers()

    assert "ETFBW20TR" in result
    query_str = mock.query.call_args[0][0]
    assert "etf_instruments" in query_str
    assert "UNION" in query_str
    assert "companies" in query_str
