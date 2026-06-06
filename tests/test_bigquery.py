from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from db.bigquery import fetch_top_n_for_window, save_analysis_result, save_post_text


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
