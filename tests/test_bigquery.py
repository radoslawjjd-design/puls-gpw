from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from db.bigquery import (
    _build_filter_clauses,
    delete_announcement,
    fetch_top_n_for_window,
    insert_announcement,
    list_announcements_admin,
    list_announcements_user,
    save_analysis_result,
    save_post_text,
    update_parsed_content,
)


def _mock_bq_client(affected_rows: int = 1) -> MagicMock:
    client = MagicMock()
    client.project = "test-project"
    job = MagicMock()
    job.result.return_value = None
    job.errors = None
    job.num_dml_affected_rows = affected_rows
    client.query.return_value = job
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


def test_save_analysis_result_null_approved_does_not_raise():
    """ScalarQueryParameter("analysis_approved", "BOOL", None) must not raise."""
    with patch("db.bigquery._get_client", return_value=_mock_bq_client()):
        save_analysis_result(
            announcement_id="abc123",
            structured_analysis='{"company": "Test"}',
            analysis_approved=None,
            analysis_reject_reason=None,
            event_type="inne",
            analysis_score=None,
        )


def test_save_analysis_result_approved_true():
    with patch("db.bigquery._get_client", return_value=_mock_bq_client()):
        save_analysis_result(
            announcement_id="abc123",
            structured_analysis='{"company": "Test"}',
            analysis_approved=True,
            analysis_reject_reason=None,
            event_type="wyniki_finansowe",
            analysis_score=125.0,
        )


# ── fetch_top_n_for_window ────────────────────────────────────────────────────

def test_fetch_top_n_for_window_returns_rows():
    row_data = [
        {
            "announcement_id": "id1", "ticker": "PKO", "company": "PKO Bank Polski",
            "title": "Wyniki Q1", "structured_analysis": '{"summary_pl": "test"}',
            "event_type": "wyniki_finansowe", "analysis_score": 125.0,
            "url": "http://example.com/1",
        },
        {
            "announcement_id": "id2", "ticker": "XTB", "company": "XTB SA",
            "title": "Wyniki Q1 XTB", "structured_analysis": '{"summary_pl": "test2"}',
            "event_type": "wyniki_finansowe", "analysis_score": 140.0,
            "url": "http://example.com/2",
        },
    ]
    start = datetime(2026, 6, 8, 6, 30, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 8, 7, 29, 0, tzinfo=timezone.utc)

    with patch("db.bigquery._get_client", return_value=_mock_bq_client_with_rows(row_data)):
        result = fetch_top_n_for_window(start, end, n=4)

    assert len(result) == 2
    assert result[0]["announcement_id"] == "id1"
    assert result[0]["ticker"] == "PKO"
    assert result[1]["ticker"] == "XTB"
    assert "structured_analysis" in result[0]
    assert "analysis_score" in result[0]


def test_fetch_top_n_for_window_empty():
    start = datetime(2026, 6, 8, 6, 30, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 8, 7, 29, 0, tzinfo=timezone.utc)

    with patch("db.bigquery._get_client", return_value=_mock_bq_client_with_rows([])):
        result = fetch_top_n_for_window(start, end)

    assert result == []


# ── save_post_text ────────────────────────────────────────────────────────────

def test_save_post_text_calls_query_with_unnest():
    with patch("db.bigquery._get_client", return_value=_mock_bq_client(affected_rows=2)) as mock_get:
        client = mock_get.return_value
        save_post_text(
            announcement_ids=["id1", "id2"],
            post_text="tweet 1\n\ntweet 2",
            supervisor_attempts=1,
        )

    assert client.query.called
    query_str = client.query.call_args[0][0]
    assert "UNNEST(@ids)" in query_str


def test_save_post_text_none_records_failure():
    with patch("db.bigquery._get_client", return_value=_mock_bq_client(affected_rows=1)) as mock_get:
        client = mock_get.return_value
        save_post_text(
            announcement_ids=["id1"],
            post_text=None,
            supervisor_attempts=3,
        )

    assert client.query.called


# ── delete_announcement ───────────────────────────────────────────────────────

def test_delete_announcement_calls_delete_query():
    with patch("db.bigquery._get_client", return_value=_mock_bq_client(affected_rows=1)) as mock_get:
        client = mock_get.return_value
        delete_announcement("abc123")

    assert client.query.called
    query_str = client.query.call_args[0][0]
    assert "DELETE FROM" in query_str
    assert "announcement_id = @id" in query_str


def test_delete_announcement_raises_on_no_match():
    from src.exceptions import BigQueryError
    with patch("db.bigquery._get_client", return_value=_mock_bq_client(affected_rows=0)):
        with pytest.raises(BigQueryError, match="no row matched"):
            delete_announcement("nonexistent-id")


# ── contract tests: field semantics per pipeline step ────────────────────────

def test_insert_announcement_omits_company_ticker():
    """INSERT must not bind company or ticker — parser sets them via update_parsed_content."""
    with patch("db.bigquery._get_client", return_value=_mock_bq_client()) as mock_get:
        client = mock_get.return_value
        insert_announcement(
            url="https://example.com/ann1",
            published_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            title="Test ogłoszenie",
        )

    query_str = client.query.call_args[0][0]
    job_config = client.query.call_args.kwargs["job_config"]
    param_names = {p.name for p in job_config.query_parameters}

    assert "@company" not in query_str
    assert "@ticker" not in query_str
    assert "company" not in param_names
    assert "ticker" not in param_names


def test_update_parsed_content_sets_three_fields():
    """update_parsed_content must write parsed_content, ticker, and company."""
    with patch("db.bigquery._get_client", return_value=_mock_bq_client()) as mock_get:
        client = mock_get.return_value
        update_parsed_content(
            announcement_id="abc123",
            parsed_content="Treść ogłoszenia...",
            ticker="PKO",
            company="PKO Bank Polski SA",
        )

    query_str = client.query.call_args[0][0]
    assert "parsed_content" in query_str
    assert "ticker" in query_str
    assert "company" in query_str


def test_save_analysis_result_stamps_analyzed_at():
    """save_analysis_result must set analyzed_at = CURRENT_TIMESTAMP() server-side."""
    with patch("db.bigquery._get_client", return_value=_mock_bq_client()) as mock_get:
        client = mock_get.return_value
        save_analysis_result(
            announcement_id="abc123",
            structured_analysis='{"summary_pl": "test"}',
            analysis_approved=True,
            analysis_reject_reason=None,
            event_type="wyniki_finansowe",
            analysis_score=125.0,
        )

    query_str = client.query.call_args[0][0]
    assert "analyzed_at = CURRENT_TIMESTAMP()" in query_str


def test_save_post_text_stamps_posted_at():
    """save_post_text must set posted_at = CURRENT_TIMESTAMP(), not processed_at."""
    with patch("db.bigquery._get_client", return_value=_mock_bq_client(affected_rows=1)) as mock_get:
        client = mock_get.return_value
        save_post_text(
            announcement_ids=["id1"],
            post_text="Tweet 1\n\nTweet 2",
            supervisor_attempts=1,
        )

    query_str = client.query.call_args[0][0]
    assert "posted_at = CURRENT_TIMESTAMP()" in query_str
    assert "processed_at" not in query_str


# ---------------------------------------------------------------------------
# Phase 1 — BQ Data Layer (auth-public-url)
# ---------------------------------------------------------------------------


def test_build_filter_clauses_no_filters_returns_empty():
    where, params = _build_filter_clauses()
    assert where == ""
    assert params == []


def test_build_filter_clauses_ticker_adds_param():
    where, params = _build_filter_clauses(ticker="PKO")
    assert "ticker = @ticker" in where
    assert any(p.name == "ticker" and p.value == "PKO" for p in params)


def test_list_announcements_admin_no_filters_selects_all():
    mock = _mock_bq_client_with_rows([{"announcement_id": "x", "ticker": "PKO"}])
    with patch("db.bigquery._get_client", return_value=mock):
        rows = list_announcements_admin(page=1, page_size=10)
    query_str = mock.query.call_args[0][0]
    assert "ORDER BY published_at DESC" in query_str
    assert len(rows) == 1 and rows[0]["ticker"] == "PKO"


def test_list_announcements_admin_ticker_filter_passes_param():
    mock = _mock_bq_client_with_rows([])
    with patch("db.bigquery._get_client", return_value=mock):
        list_announcements_admin(page=1, page_size=5, ticker="CDR")
    job_config = mock.query.call_args[1]["job_config"]
    names = [p.name for p in job_config.query_parameters]
    assert "ticker" in names


def test_list_announcements_user_only_approved():
    mock = _mock_bq_client_with_rows([{"ticker": "PKO", "company": "PKO Bank"}])
    with patch("db.bigquery._get_client", return_value=mock):
        rows = list_announcements_user(page=1, page_size=10)
    query_str = mock.query.call_args[0][0]
    assert "analysis_approved = TRUE" in query_str
    assert len(rows) == 1


def test_list_announcements_user_ticker_filter_passes_param():
    mock = _mock_bq_client_with_rows([])
    with patch("db.bigquery._get_client", return_value=mock):
        list_announcements_user(page=1, page_size=5, ticker="CDR")
    job_config = mock.query.call_args[1]["job_config"]
    names = [p.name for p in job_config.query_parameters]
    assert "ticker" in names


def test_list_announcements_admin_offset_math():
    """page=2, page_size=1 must produce OFFSET 1 in the BQ query parameters."""
    mock = _mock_bq_client_with_rows([])
    with patch("db.bigquery._get_client", return_value=mock):
        list_announcements_admin(page=2, page_size=1)
    job_config = mock.query.call_args[1]["job_config"]
    params_by_name = {p.name: p.value for p in job_config.query_parameters}
    assert params_by_name["page_size"] == 1
    assert params_by_name["offset"] == 1


def test_list_announcements_user_offset_math():
    """page=3, page_size=20 must produce OFFSET 40 in the BQ query parameters."""
    mock = _mock_bq_client_with_rows([])
    with patch("db.bigquery._get_client", return_value=mock):
        list_announcements_user(page=3, page_size=20)
    job_config = mock.query.call_args[1]["job_config"]
    params_by_name = {p.name: p.value for p in job_config.query_parameters}
    assert params_by_name["page_size"] == 20
    assert params_by_name["offset"] == 40
