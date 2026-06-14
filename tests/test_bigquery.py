from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from db.bigquery import (
    _build_filter_clauses,
    create_x_posts_table_if_not_exists,
    delete_announcement,
    fetch_top_n_for_window,
    insert_announcement,
    list_announcements_admin,
    list_announcements_user,
    save_analysis_result,
    save_x_post,
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


# ── x_posts table + save_x_post (PUL-29) ──────────────────────────────────────

def test_create_x_posts_table_creates_on_not_found():
    """create_x_posts_table_if_not_exists must create the table when get_table raises NotFound."""
    from google.cloud.exceptions import NotFound

    client = MagicMock()
    client.project = "test-project"
    client.get_table.side_effect = NotFound("missing")

    with patch("db.bigquery._get_client", return_value=client):
        create_x_posts_table_if_not_exists()

    assert client.create_table.called
    created_table = client.create_table.call_args[0][0]
    assert "x_posts" in str(created_table.reference) or "x_posts" in str(created_table)


def test_save_x_post_inserts_xpost_and_links_announcements():
    """save_x_post must INSERT one x_posts row (posted_at server-side) and UPDATE x_post_id."""
    with patch("db.bigquery._get_client", return_value=_mock_bq_client(affected_rows=2)) as mock_get:
        client = mock_get.return_value
        x_post_id = save_x_post(
            announcement_ids=["id1", "id2"],
            post_text="tweet 1\n\ntweet 2",
            window="poludnie",
            supervisor_attempts=1,
        )

    assert isinstance(x_post_id, str) and x_post_id
    queries = [call.args[0] for call in client.query.call_args_list]
    assert len(queries) == 2
    insert_q = next(q for q in queries if "INSERT" in q and "x_posts" in q)
    update_q = next(q for q in queries if "UPDATE" in q)
    assert "posted_at" in insert_q and "CURRENT_TIMESTAMP()" in insert_q
    assert "x_post_id" in update_q
    assert "IN UNNEST(@ids)" in update_q


def test_save_x_post_raises_on_no_announcements_updated():
    """save_x_post must raise BigQueryError when the announcements UPDATE matches 0 rows."""
    from src.exceptions import BigQueryError

    with patch("db.bigquery._get_client", return_value=_mock_bq_client(affected_rows=0)):
        with pytest.raises(BigQueryError):
            save_x_post(
                announcement_ids=["missing"],
                post_text="t",
                window="ranek",
                supervisor_attempts=2,
            )


def test_list_announcements_admin_joins_x_posts_and_exposes_x_post_id():
    """Admin list must LEFT JOIN x_posts (so new posts' text shows) and return x_post_id."""
    row = {
        "announcement_id": "id1", "url": "http://x/1", "published_at": None,
        "title": "T", "company": "C", "ticker": "PKO", "post_text": "tweet",
        "posted_at": None, "analyzed_at": None, "supervisor_attempts": 1,
        "parsed_content": None, "priority": None, "structured_analysis": None,
        "analysis_approved": True, "analysis_reject_reason": None,
        "event_type": "wyniki_finansowe", "analysis_score": 1.0, "x_post_id": "abc",
    }
    with patch("db.bigquery._get_client", return_value=_mock_bq_client_with_rows([row])) as mock_get:
        client = mock_get.return_value
        result = list_announcements_admin(page=1, page_size=20)

    query_str = client.query.call_args[0][0]
    assert "LEFT JOIN" in query_str
    assert "x_posts" in query_str
    assert result[0]["x_post_id"] == "abc"


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
    assert "ORDER BY a.published_at DESC" in query_str
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
