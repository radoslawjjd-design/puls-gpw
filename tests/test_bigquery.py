from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from db.bigquery import (
    _COMPANIES_SCHEMA,
    _COMPANY_DAILY_STATS_SCHEMA,
    _WATCHLIST_SCHEMA,
    _X_POSTS_SCHEMA,
    _build_filter_clauses,
    add_watchlist_ticker,
    create_companies_table_if_not_exists,
    batch_insert_company_daily_stats,
    create_company_daily_stats_table_if_not_exists,
    create_portfolio_snapshots_table_if_not_exists,
    delete_company_daily_stats_for_date,
    create_watchlist_table_if_not_exists,
    create_x_posts_table_if_not_exists,
    delete_announcement,
    fetch_top_n_for_window,
    get_latest_snapshot_before,
    get_latest_snapshot_for_wallet,
    insert_announcement,
    list_announcements_admin,
    list_announcements_for_watchlist,
    list_announcements_user,
    get_latest_company_stats_fetched_at,
    list_companies_with_hop_info,
    merge_company_daily_stats,
    list_distinct_companies,
    list_distinct_tickers,
    list_tickers_missing_from_companies,
    list_watchlist_tickers,
    list_x_posts_admin,
    remove_watchlist_ticker,
    save_analysis_result,
    save_portfolio_snapshot,
    save_x_post,
    update_parsed_content,
    update_x_post_publish_result,
    upsert_company,
    x_post_already_published,
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
            "title": "Wyniki Q1",
            "structured_analysis": '{"summary_pl": "test", "key_numbers": ["zysk 1 mld zł"]}',
            "event_type": "wyniki_finansowe", "analysis_score": 125.0,
            "url": "http://example.com/1",
        },
        {
            "announcement_id": "id2", "ticker": "XTB", "company": "XTB SA",
            "title": "Wyniki Q1 XTB",
            "structured_analysis": '{"summary_pl": "test2", "key_numbers": ["przychody 500 mln zł"]}',
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


def test_fetch_top_n_for_window_filters_by_min_score():
    """PUL-27 quality gate: the query must filter analysis_score >= @min_score."""
    start = datetime(2026, 6, 8, 6, 30, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 8, 7, 29, 0, tzinfo=timezone.utc)

    with patch("db.bigquery._get_client", return_value=_mock_bq_client_with_rows([])) as mock_get:
        client = mock_get.return_value
        fetch_top_n_for_window(start, end, n=4, min_score=50)

    query_str = client.query.call_args[0][0]
    assert "analysis_score >= @min_score" in query_str
    params = {p.name: p.value for p in client.query.call_args.kwargs["job_config"].query_parameters}
    assert params["min_score"] == 50


def test_fetch_top_n_for_window_excludes_inne():
    start = datetime(2026, 6, 8, 6, 30, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 8, 7, 29, 0, tzinfo=timezone.utc)

    mock = _mock_bq_client_with_rows([])
    with patch("db.bigquery._get_client", return_value=mock):
        fetch_top_n_for_window(start, end)

    query_str = mock.query.call_args[0][0]
    assert "event_type != 'inne'" in query_str


def test_fetch_top_n_for_window_orders_by_published_at_tiebreak():
    """PUL-40: deterministic tie-break requires ORDER BY score DESC, published_at DESC + safety cap."""
    start = datetime(2026, 6, 8, 6, 30, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 8, 7, 29, 0, tzinfo=timezone.utc)

    mock = _mock_bq_client_with_rows([])
    with patch("db.bigquery._get_client", return_value=mock):
        fetch_top_n_for_window(start, end)

    query_str = mock.query.call_args[0][0]
    assert "ORDER BY analysis_score DESC, published_at DESC" in query_str
    from db.bigquery import _FETCH_SAFETY_CAP
    assert f"LIMIT {_FETCH_SAFETY_CAP}" in query_str


def test_fetch_top_n_for_window_dedups_to_distinct_companies():
    """PUL-40: dedup-before-limit — N raw rows of one ticker yield 1 company, slot backfills."""
    row_data = [
        {
            "announcement_id": f"tow{i}", "ticker": "TOW", "company": "TOWERINVT",
            "title": f"Wyniki {i}",
            "structured_analysis": '{"key_numbers": ["zysk 1 mln zł"]}',
            "event_type": "wyniki_finansowe", "analysis_score": 120.0,
            "url": f"http://example.com/tow{i}",
        }
        for i in range(7)
    ]
    row_data.append({
        "announcement_id": "asb1", "ticker": "ASB", "company": "ASB SA",
        "title": "Kontrakt znaczący",
        "structured_analysis": '{"summary_pl": "umowa"}',
        "event_type": "kontrakt_znaczacy", "analysis_score": 120.0,
        "url": "http://example.com/asb1",
    })
    start = datetime(2026, 6, 8, 6, 30, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 8, 7, 29, 0, tzinfo=timezone.utc)

    with patch("db.bigquery._get_client", return_value=_mock_bq_client_with_rows(row_data)):
        result = fetch_top_n_for_window(start, end, n=4)

    tickers = [r["ticker"] for r in result]
    assert tickers == ["TOW", "ASB"]


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
    # `window` is a BQ reserved keyword — the column identifier must be backtick-quoted
    assert "`window`" in insert_q
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


# ── x_publish_status persistence + idempotency (PUL-26) ───────────────────────

def test_x_posts_schema_has_publish_status_column():
    """The x_publish_status STRING column must be in _X_POSTS_SCHEMA (NULLABLE)."""
    field = next((f for f in _X_POSTS_SCHEMA if f.name == "x_publish_status"), None)
    assert field is not None, "x_publish_status missing from _X_POSTS_SCHEMA"
    assert field.field_type == "STRING"
    assert field.mode == "NULLABLE"


def test_update_x_post_publish_result_writes_status_and_joined_ids():
    """UPDATE x_posts SET tweet_ids + x_publish_status, keyed by x_post_id."""
    with patch("db.bigquery._get_client", return_value=_mock_bq_client(affected_rows=1)) as mock_get:
        client = mock_get.return_value
        update_x_post_publish_result("xp1", ["111", "222", "333"], "published")

    query_str = client.query.call_args[0][0]
    job_config = client.query.call_args.kwargs["job_config"]
    params = {p.name: p.value for p in job_config.query_parameters}
    assert "UPDATE" in query_str and "x_posts" in query_str
    assert "x_publish_status = @status" in query_str
    assert "WHERE x_post_id = @x_post_id" in query_str
    # ids joined comma-separated into the STRING column
    assert params["tweet_ids"] == "111,222,333"
    assert params["status"] == "published"
    assert params["x_post_id"] == "xp1"


# ── portfolio_snapshots table (PUL-39) ────────────────────────────────────────

def test_create_portfolio_snapshots_table_creates_on_not_found():
    """create_portfolio_snapshots_table_if_not_exists must create the table when get_table raises NotFound."""
    from google.cloud.exceptions import NotFound

    client = MagicMock()
    client.project = "test-project"
    client.get_table.side_effect = NotFound("missing")

    with patch("db.bigquery._get_client", return_value=client):
        create_portfolio_snapshots_table_if_not_exists()

    assert client.create_table.called
    created_table = client.create_table.call_args[0][0]
    assert "portfolio_snapshots" in str(created_table.reference) or "portfolio_snapshots" in str(created_table)


def test_save_portfolio_snapshot_inserts_row_and_returns_id():
    """save_portfolio_snapshot must INSERT one portfolio_snapshots row (created_at server-side)."""
    with patch("db.bigquery._get_client", return_value=_mock_bq_client()) as mock_get:
        client = mock_get.return_value
        snapshot_id = save_portfolio_snapshot(
            wallet="main",
            snapshot_date=date(2026, 6, 17),
            total_value=12345.67,
            currency="PLN",
            day_change_abs=100.0,
            day_change_pct=0.81,
            positions_json='[{"ticker": "PKO", "value": 1000.0, "pct": 8.1}]',
        )

    assert isinstance(snapshot_id, str) and snapshot_id
    query_str = client.query.call_args[0][0]
    assert "INSERT" in query_str and "portfolio_snapshots" in query_str
    assert "created_at" in query_str and "CURRENT_TIMESTAMP()" in query_str
    params = {p.name: p.value for p in client.query.call_args.kwargs["job_config"].query_parameters}
    assert params["wallet"] == "main"
    assert params["snapshot_date"] == date(2026, 6, 17)
    assert params["total_value"] == 12345.67


def test_get_latest_snapshot_before_returns_prior_row():
    """get_latest_snapshot_before must filter snapshot_date < before_date (strict, not <=)."""
    row_data = [{
        "snapshot_id": "snap1", "wallet": "main", "snapshot_date": date(2026, 6, 16),
        "total_value": 12000.0, "currency": "PLN",
        "day_change_abs": 50.0, "day_change_pct": 0.42,
        "positions_json": None,
    }]
    with patch("db.bigquery._get_client", return_value=_mock_bq_client_with_rows(row_data)) as mock_get:
        client = mock_get.return_value
        result = get_latest_snapshot_before("main", date(2026, 6, 17))

    assert result is not None
    assert result["wallet"] == "main"
    assert result["total_value"] == 12000.0
    query_str = client.query.call_args[0][0]
    assert "snapshot_date < @before_date" in query_str


def test_get_latest_snapshot_before_returns_none_when_no_prior_row():
    """First-ever run for a wallet has no prior snapshot — must return None, not raise."""
    with patch("db.bigquery._get_client", return_value=_mock_bq_client_with_rows([])):
        result = get_latest_snapshot_before("ikze", date(2026, 6, 17))

    assert result is None


def test_get_latest_snapshot_for_wallet_returns_most_recent_row():
    """get_latest_snapshot_for_wallet must bind wallet and order by snapshot_date DESC, created_at DESC."""
    row_data = [{
        "snapshot_id": "snap2", "wallet": "ikze", "snapshot_date": date(2026, 6, 19),
        "total_value": 5000.0, "currency": "PLN",
        "day_change_abs": 10.0, "day_change_pct": 0.2,
        "positions_json": '{"positions": [], "media_attached": false}',
    }]
    with patch("db.bigquery._get_client", return_value=_mock_bq_client_with_rows(row_data)) as mock_get:
        client = mock_get.return_value
        result = get_latest_snapshot_for_wallet("ikze")

    assert result is not None
    assert result["wallet"] == "ikze"
    assert result["snapshot_date"] == date(2026, 6, 19)
    query_str = client.query.call_args[0][0]
    assert "WHERE wallet = @wallet" in query_str
    assert "ORDER BY snapshot_date DESC, created_at DESC" in query_str
    params = {p.name: p.value for p in client.query.call_args.kwargs["job_config"].query_parameters}
    assert params["wallet"] == "ikze"


def test_get_latest_snapshot_for_wallet_returns_none_when_no_rows():
    """No /portfolio-xpost run has ever happened for this wallet — must return None, not raise."""
    with patch("db.bigquery._get_client", return_value=_mock_bq_client_with_rows([])):
        result = get_latest_snapshot_for_wallet("main")

    assert result is None


def test_update_x_post_publish_result_none_ids_stores_null():
    """tweet_ids=None (e.g. skipped/failed) must bind NULL, not the string 'None'."""
    with patch("db.bigquery._get_client", return_value=_mock_bq_client(affected_rows=1)) as mock_get:
        client = mock_get.return_value
        update_x_post_publish_result("xp1", None, "skipped")

    job_config = client.query.call_args.kwargs["job_config"]
    params = {p.name: p.value for p in job_config.query_parameters}
    assert params["tweet_ids"] is None
    assert params["status"] == "skipped"


def test_update_x_post_publish_result_raises_on_no_match():
    from src.exceptions import BigQueryError
    with patch("db.bigquery._get_client", return_value=_mock_bq_client(affected_rows=0)):
        with pytest.raises(BigQueryError):
            update_x_post_publish_result("missing", ["1"], "published")


def test_x_post_already_published_backticks_window_and_keys_on_warsaw_day():
    """SELECT must backtick `window`, gate on published status + DATE(posted_at) Warsaw."""
    from datetime import date

    with patch("db.bigquery._get_client", return_value=_mock_bq_client_with_rows([{"cnt": 1}])) as mock_get:
        client = mock_get.return_value
        result = x_post_already_published("poludnie", day=date(2026, 6, 15))

    query_str = client.query.call_args[0][0]
    job_config = client.query.call_args.kwargs["job_config"]
    params = {p.name: p.value for p in job_config.query_parameters}
    assert result is True
    # `window` is a BQ reserved keyword — must be backtick-quoted in the SELECT
    assert "`window` = @window" in query_str
    assert "x_publish_status = 'published'" in query_str
    assert "DATE(posted_at, 'Europe/Warsaw') = @day" in query_str
    assert params["window"] == "poludnie"
    assert params["day"] == date(2026, 6, 15)


def test_x_post_already_published_false_when_no_rows():
    with patch("db.bigquery._get_client", return_value=_mock_bq_client_with_rows([{"cnt": 0}])):
        assert x_post_already_published("ranek", day=None) is False


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
    assert "analysis_score" not in query_str
    assert "analysis_score" not in rows[0]


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


# ── list_x_posts_admin (admin-ui-x-post-history) ──────────────────────────────

def test_list_x_posts_admin_no_filters_selects_all_orders_newest_first():
    mock = _mock_bq_client_with_rows([{"x_post_id": "p1", "window": "ranek"}])
    with patch("db.bigquery._get_client", return_value=mock):
        rows = list_x_posts_admin(page=1, page_size=10)
    query_str = mock.query.call_args[0][0]
    assert "ORDER BY posted_at DESC" in query_str
    assert len(rows) == 1 and rows[0]["x_post_id"] == "p1"


def test_list_x_posts_admin_backticks_window_column():
    """`window` is a BQ reserved keyword — must be backticked in SELECT and WHERE (PUL-29)."""
    mock = _mock_bq_client_with_rows([])
    with patch("db.bigquery._get_client", return_value=mock):
        list_x_posts_admin(page=1, page_size=10, window="ranek")
    query_str = mock.query.call_args[0][0]
    assert "`window`" in query_str


def test_list_x_posts_admin_filters_pass_params():
    from datetime import datetime, timezone
    mock = _mock_bq_client_with_rows([])
    with patch("db.bigquery._get_client", return_value=mock):
        list_x_posts_admin(
            page=1, page_size=10, window="ranek", x_publish_status="published",
            post_text="PASSUS",
            from_dt=datetime(2026, 6, 1, tzinfo=timezone.utc),
            to_dt=datetime(2026, 6, 19, tzinfo=timezone.utc),
        )
    query_str = mock.query.call_args[0][0]
    job_config = mock.query.call_args.kwargs["job_config"]
    params_by_name = {p.name: p.value for p in job_config.query_parameters}
    assert "`window` = @window" in query_str
    assert "x_publish_status = @x_publish_status" in query_str
    assert "LOWER(post_text) LIKE LOWER(@post_text)" in query_str
    assert "posted_at >= @from_dt" in query_str
    assert "posted_at <= @to_dt" in query_str
    assert params_by_name["window"] == "ranek"
    assert params_by_name["x_publish_status"] == "published"
    assert params_by_name["post_text"] == "%PASSUS%"


def test_list_x_posts_admin_offset_math():
    mock = _mock_bq_client_with_rows([])
    with patch("db.bigquery._get_client", return_value=mock):
        list_x_posts_admin(page=3, page_size=20)
    job_config = mock.query.call_args.kwargs["job_config"]
    params_by_name = {p.name: p.value for p in job_config.query_parameters}
    assert params_by_name["page_size"] == 20
    assert params_by_name["offset"] == 40


# ── autocomplete BQ functions (PUL-25 panel-ui-redesign) ──────────────────────

def test_list_distinct_tickers_returns_sorted_list():
    """list_distinct_tickers must read from companies, not announcements."""
    rows = [{"ticker": "PKO"}, {"ticker": "CDR"}, {"ticker": "XTB"}]
    mock = _mock_bq_client_with_rows(rows)
    with patch("db.bigquery._get_client", return_value=mock):
        result = list_distinct_tickers()
    assert result == ["PKO", "CDR", "XTB"]
    query_str = mock.query.call_args[0][0]
    assert "FROM" in query_str
    assert "companies" in query_str
    assert "ORDER BY ticker" in query_str


def test_list_distinct_tickers_empty_result():
    """list_distinct_tickers must return empty list when no rows."""
    mock = _mock_bq_client_with_rows([])
    with patch("db.bigquery._get_client", return_value=mock):
        result = list_distinct_tickers()
    assert result == []


def test_list_distinct_companies_returns_sorted_list():
    """list_distinct_companies must read names from companies, with no LIMIT clause."""
    rows = [{"name": "Alior Bank SA"}, {"name": "PKO Bank Polski SA"}]
    mock = _mock_bq_client_with_rows(rows)
    with patch("db.bigquery._get_client", return_value=mock):
        result = list_distinct_companies()
    assert result == ["Alior Bank SA", "PKO Bank Polski SA"]
    query_str = mock.query.call_args[0][0]
    assert "companies" in query_str
    assert "name IS NOT NULL" in query_str
    assert "ORDER BY name" in query_str
    assert "LIMIT" not in query_str


def test_list_distinct_companies_empty_result():
    """list_distinct_companies must return empty list when no rows."""
    mock = _mock_bq_client_with_rows([])
    with patch("db.bigquery._get_client", return_value=mock):
        result = list_distinct_companies()
    assert result == []


# ── watchlist table + CRUD (PUL-28 my-wallet-watchlist) ───────────────────────

def test_create_watchlist_table_creates_on_not_found():
    """create_watchlist_table_if_not_exists must create the table when get_table raises NotFound."""
    from google.cloud.exceptions import NotFound

    client = MagicMock()
    client.project = "test-project"
    client.get_table.side_effect = NotFound("missing")

    with patch("db.bigquery._get_client", return_value=client):
        create_watchlist_table_if_not_exists()

    assert client.create_table.called
    created_table = client.create_table.call_args[0][0]
    assert "watchlist" in str(created_table.reference) or "watchlist" in str(created_table)


def test_watchlist_schema_has_required_columns():
    """_WATCHLIST_SCHEMA must define client_id, ticker, added_at — all REQUIRED."""
    names = {f.name: f for f in _WATCHLIST_SCHEMA}
    assert set(names) == {"client_id", "ticker", "added_at"}
    assert all(f.mode == "REQUIRED" for f in names.values())


def test_add_watchlist_ticker_inserts_with_not_exists_guard():
    """INSERT must be guarded by WHERE NOT EXISTS so re-adding is a silent no-op."""
    with patch("db.bigquery._get_client", return_value=_mock_bq_client()) as mock_get:
        client = mock_get.return_value
        add_watchlist_ticker("client1", "PKO")

    query_str = client.query.call_args[0][0]
    job_config = client.query.call_args.kwargs["job_config"]
    params = {p.name: p.value for p in job_config.query_parameters}
    assert "INSERT INTO" in query_str and "watchlist" in query_str
    assert "WHERE NOT EXISTS" in query_str
    assert "CURRENT_TIMESTAMP()" in query_str
    assert params["client_id"] == "client1"
    assert params["ticker"] == "PKO"


def test_remove_watchlist_ticker_deletes_by_composite_key():
    """remove_watchlist_ticker must DELETE filtered by client_id AND ticker; 0 rows is not an error."""
    with patch("db.bigquery._get_client", return_value=_mock_bq_client(affected_rows=0)) as mock_get:
        client = mock_get.return_value
        remove_watchlist_ticker("client1", "NEVER_ADDED")

    query_str = client.query.call_args[0][0]
    assert "DELETE FROM" in query_str
    assert "client_id = @client_id AND ticker = @ticker" in query_str


def test_list_watchlist_tickers_returns_only_calling_clients_rows():
    """list_watchlist_tickers must filter by client_id and order by added_at DESC."""
    rows = [{"ticker": "XTB"}, {"ticker": "PKO"}]
    mock = _mock_bq_client_with_rows(rows)
    with patch("db.bigquery._get_client", return_value=mock) as mock_get:
        client = mock_get.return_value
        result = list_watchlist_tickers("client1")

    assert result == ["XTB", "PKO"]
    query_str = client.query.call_args[0][0]
    job_config = client.query.call_args.kwargs["job_config"]
    params = {p.name: p.value for p in job_config.query_parameters}
    assert "WHERE client_id = @client_id" in query_str
    assert "ORDER BY added_at DESC" in query_str
    assert params["client_id"] == "client1"


def test_list_announcements_for_watchlist_includes_bounded_join():
    """The watchlist-filtered query must INNER JOIN a watchlist subquery bounded to 200 tickers."""
    mock = _mock_bq_client_with_rows([{"company": "PKO Bank", "ticker": "PKO"}])
    with patch("db.bigquery._get_client", return_value=mock) as mock_get:
        client = mock_get.return_value
        rows = list_announcements_for_watchlist("client1", page=1, page_size=10)

    query_str = client.query.call_args[0][0]
    job_config = client.query.call_args.kwargs["job_config"]
    params = {p.name: p.value for p in job_config.query_parameters}
    assert "INNER JOIN" in query_str
    assert "LIMIT 200" in query_str
    assert "a.ticker = w.ticker" in query_str
    assert "analysis_approved = TRUE" in query_str
    assert params["client_id"] == "client1"
    assert len(rows) == 1 and rows[0]["ticker"] == "PKO"


def test_list_announcements_for_watchlist_offset_math():
    """page=3, page_size=20 must produce OFFSET 40 in the BQ query parameters."""
    mock = _mock_bq_client_with_rows([])
    with patch("db.bigquery._get_client", return_value=mock):
        list_announcements_for_watchlist("client1", page=3, page_size=20)
    job_config = mock.query.call_args.kwargs["job_config"]
    params_by_name = {p.name: p.value for p in job_config.query_parameters}
    assert params_by_name["page_size"] == 20
    assert params_by_name["offset"] == 40


# ── companies dimension table (PUL-53) ────────────────────────────────────────

def test_create_companies_table_creates_on_not_found():
    """create_companies_table_if_not_exists must create the table when get_table raises NotFound."""
    from google.cloud.exceptions import NotFound

    client = MagicMock()
    client.project = "test-project"
    client.get_table.side_effect = NotFound("missing")

    with patch("db.bigquery._get_client", return_value=client):
        create_companies_table_if_not_exists()

    assert client.create_table.called
    created_table = client.create_table.call_args[0][0]
    assert "companies" in str(created_table.reference) or "companies" in str(created_table)


def test_companies_schema_has_required_columns():
    """_COMPANIES_SCHEMA must define ticker, name, hop_url, isin, created_at, updated_at."""
    names = {f.name: f for f in _COMPANIES_SCHEMA}
    assert set(names) == {"ticker", "name", "hop_url", "isin", "created_at", "updated_at"}
    assert names["ticker"].mode == "REQUIRED"
    assert names["created_at"].mode == "REQUIRED"
    assert names["updated_at"].mode == "REQUIRED"
    assert names["name"].mode == "NULLABLE"
    assert names["hop_url"].mode == "NULLABLE"
    assert names["isin"].mode == "NULLABLE"


def test_upsert_company_sends_merge_with_all_fields():
    """upsert_company must issue a MERGE statement binding all four scalar fields."""
    with patch("db.bigquery._get_client", return_value=_mock_bq_client()) as mock_get:
        client = mock_get.return_value
        upsert_company("ECH", "Echo Investment SA", "https://example.com/profile", "PLECHPS00019")

    query_str = client.query.call_args[0][0]
    job_config = client.query.call_args.kwargs["job_config"]
    params = {p.name: p.value for p in job_config.query_parameters}
    assert "MERGE" in query_str
    assert "companies" in query_str
    assert params["ticker"] == "ECH"
    assert params["name"] == "Echo Investment SA"
    assert params["hop_url"] == "https://example.com/profile"
    assert params["isin"] == "PLECHPS00019"


def test_list_tickers_missing_from_companies_returns_pairs():
    """Must return (ticker, fallback_name) tuples and reference both tables via NOT EXISTS."""
    rows = [{"ticker": "PKP", "fallback_name": "PKP Cargo SA"}, {"ticker": "ROB", "fallback_name": None}]
    mock = _mock_bq_client_with_rows(rows)
    with patch("db.bigquery._get_client", return_value=mock):
        result = list_tickers_missing_from_companies()
    assert result == [("PKP", "PKP Cargo SA"), ("ROB", None)]
    query_str = mock.query.call_args[0][0]
    assert "NOT EXISTS" in query_str
    assert "announcements" in query_str
    assert "companies" in query_str


def test_list_tickers_missing_from_companies_empty_result():
    """Must return an empty list when every announcements ticker already has a companies row."""
    mock = _mock_bq_client_with_rows([])
    with patch("db.bigquery._get_client", return_value=mock):
        result = list_tickers_missing_from_companies()
    assert result == []


# ── company_daily_stats table (PUL-54) ────────────────────────────────────────

def test_company_daily_stats_schema_has_required_columns():
    """_COMPANY_DAILY_STATS_SCHEMA must define all expected fields with correct modes."""
    names = {f.name: f for f in _COMPANY_DAILY_STATS_SCHEMA}
    assert set(names) == {
        "ticker", "snapshot_date",
        "kurs_zamkniecia", "zmiana_procentowa", "zmiana_kwotowa",
        "kurs_otwarcia", "kurs_min", "kurs_max",
        "wartosc_obrotu", "liczba_transakcji", "fetched_at",
    }
    assert names["ticker"].mode == "REQUIRED"
    assert names["snapshot_date"].mode == "REQUIRED"
    assert names["fetched_at"].mode == "REQUIRED"
    assert names["kurs_zamkniecia"].mode == "NULLABLE"
    assert names["liczba_transakcji"].field_type in ("INTEGER", "INT64")
    assert names["snapshot_date"].field_type == "DATE"
    assert names["fetched_at"].field_type == "TIMESTAMP"


def test_create_company_daily_stats_table_creates_with_partitioning_and_clustering():
    """create_company_daily_stats_table_if_not_exists must set time_partitioning + clustering."""
    from google.cloud.exceptions import NotFound

    client = MagicMock()
    client.project = "test-project"
    client.get_table.side_effect = NotFound("missing")

    with patch("db.bigquery._get_client", return_value=client):
        create_company_daily_stats_table_if_not_exists()

    assert client.create_table.called
    created_table = client.create_table.call_args[0][0]
    assert "company_daily_stats" in str(created_table.reference) or "company_daily_stats" in str(created_table)
    assert created_table.time_partitioning is not None
    assert created_table.time_partitioning.field == "snapshot_date"
    assert created_table.clustering_fields == ["ticker"]


def test_create_company_daily_stats_table_no_op_if_exists():
    """create_company_daily_stats_table_if_not_exists must not call create_table when table exists."""
    client = MagicMock()
    client.project = "test-project"
    client.get_table.return_value = MagicMock()

    with patch("db.bigquery._get_client", return_value=client):
        create_company_daily_stats_table_if_not_exists()

    assert not client.create_table.called


def test_list_companies_with_hop_info_returns_dicts():
    """list_companies_with_hop_info must return list of dicts with ticker/name/hop_url/isin."""
    rows = [
        {"ticker": "ECH", "name": "Echo Investment SA", "hop_url": "https://bankier.pl/echo", "isin": "PLECHPS00019"},
        {"ticker": "PKO", "name": "PKO Bank Polski SA", "hop_url": None, "isin": "PLPKO0000016"},
    ]
    mock = _mock_bq_client_with_rows(rows)
    with patch("db.bigquery._get_client", return_value=mock):
        result = list_companies_with_hop_info()

    assert len(result) == 2
    assert result[0] == {"ticker": "ECH", "name": "Echo Investment SA", "hop_url": "https://bankier.pl/echo", "isin": "PLECHPS00019"}
    assert result[1] == {"ticker": "PKO", "name": "PKO Bank Polski SA", "hop_url": None, "isin": "PLPKO0000016"}
    query_str = mock.query.call_args[0][0]
    assert "companies" in query_str
    assert "ticker" in query_str and "hop_url" in query_str and "isin" in query_str
    assert "WHERE" not in query_str


def test_list_companies_with_hop_info_empty():
    """list_companies_with_hop_info must return empty list when companies table is empty."""
    mock = _mock_bq_client_with_rows([])
    with patch("db.bigquery._get_client", return_value=mock):
        result = list_companies_with_hop_info()
    assert result == []


def test_delete_company_daily_stats_for_date_issues_delete():
    """delete_company_daily_stats_for_date must issue DELETE for the given date."""
    with patch("db.bigquery._get_client", return_value=_mock_bq_client()) as mock_get:
        client = mock_get.return_value
        delete_company_daily_stats_for_date(date(2026, 6, 26))

    query_str = client.query.call_args[0][0]
    params = {p.name: p.value for p in client.query.call_args.kwargs["job_config"].query_parameters}
    assert "DELETE FROM" in query_str and "company_daily_stats" in query_str
    assert params["snapshot_date"] == date(2026, 6, 26)


def test_delete_company_daily_stats_raises_bigquery_error_on_failure():
    from src.exceptions import BigQueryError

    client = MagicMock()
    client.project = "test-project"
    client.query.side_effect = Exception("bq down")

    with patch("db.bigquery._get_client", return_value=client):
        with pytest.raises(BigQueryError, match="delete_company_daily_stats_for_date failed"):
            delete_company_daily_stats_for_date(date(2026, 6, 26))


def test_batch_insert_company_daily_stats_calls_insert_rows_json():
    """batch_insert_company_daily_stats must call insert_rows_json with all rows."""
    rows = [
        {"ticker": "PKO", "snapshot_date": "2026-06-26", "kurs_zamkniecia": 103.62,
         "fetched_at": "2026-06-26T17:00:00+00:00"},
        {"ticker": "CDR", "snapshot_date": "2026-06-26", "kurs_zamkniecia": 217.4,
         "fetched_at": "2026-06-26T17:00:00+00:00"},
    ]
    client = MagicMock()
    client.project = "test-project"
    client.insert_rows_json.return_value = []

    with patch("db.bigquery._get_client", return_value=client):
        batch_insert_company_daily_stats(rows)

    client.insert_rows_json.assert_called_once()
    called_rows = client.insert_rows_json.call_args[0][1]
    assert len(called_rows) == 2
    assert called_rows[0]["ticker"] == "PKO"


def test_batch_insert_company_daily_stats_empty_rows_is_noop():
    client = MagicMock()
    with patch("db.bigquery._get_client", return_value=client):
        batch_insert_company_daily_stats([])
    client.insert_rows_json.assert_not_called()


def test_batch_insert_company_daily_stats_raises_on_row_errors():
    from src.exceptions import BigQueryError

    client = MagicMock()
    client.project = "test-project"
    client.insert_rows_json.return_value = [{"errors": [{"reason": "invalid"}], "index": 0}]

    with patch("db.bigquery._get_client", return_value=client):
        with pytest.raises(BigQueryError, match="batch_insert_company_daily_stats failed"):
            batch_insert_company_daily_stats([{"ticker": "X", "snapshot_date": "2026-06-26"}])


# ── merge_company_daily_stats (company-stats-upsert) ──────────────────────────

def test_merge_company_daily_stats_happy_path():
    """merge_company_daily_stats must call load_table_from_json, query (MERGE), and delete_table."""
    from src.exceptions import BigQueryError

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

    rows = [{"ticker": "PKO", "snapshot_date": "2026-06-27", "kurs_zamkniecia": 40.0,
             "fetched_at": "2026-06-27T09:01:00+00:00"}]

    with patch("db.bigquery._get_client", return_value=client):
        merge_company_daily_stats(rows)

    client.load_table_from_json.assert_called_once()
    client.query.assert_called_once()
    merge_sql = client.query.call_args[0][0]
    assert "MERGE" in merge_sql and "company_daily_stats" in merge_sql
    client.delete_table.assert_called_once()


def test_merge_company_daily_stats_empty_rows_is_noop():
    """merge_company_daily_stats must return immediately without any BQ calls when rows is empty."""
    client = MagicMock()
    client.project = "test-project"

    with patch("db.bigquery._get_client", return_value=client):
        merge_company_daily_stats([])

    client.load_table_from_json.assert_not_called()
    client.query.assert_not_called()
    client.delete_table.assert_not_called()


def test_merge_company_daily_stats_load_failure_raises_and_cleans_up():
    """merge_company_daily_stats must raise BigQueryError and call delete_table if load fails."""
    from src.exceptions import BigQueryError

    client = MagicMock()
    client.project = "test-project"
    load_job = MagicMock()
    load_job.result.return_value = None
    load_job.errors = [{"reason": "invalid", "message": "bad data"}]
    client.load_table_from_json.return_value = load_job

    rows = [{"ticker": "PKO", "snapshot_date": "2026-06-27"}]

    with patch("db.bigquery._get_client", return_value=client):
        with pytest.raises(BigQueryError, match="load failed"):
            merge_company_daily_stats(rows)

    client.delete_table.assert_called_once()
    assert "_tmp_" in client.delete_table.call_args.args[0]
    assert client.delete_table.call_args.kwargs.get("not_found_ok") is True


def test_merge_company_daily_stats_merge_failure_raises_and_cleans_up():
    """merge_company_daily_stats must raise BigQueryError and call delete_table if MERGE fails."""
    from src.exceptions import BigQueryError

    client = MagicMock()
    client.project = "test-project"
    load_job = MagicMock()
    load_job.result.return_value = None
    load_job.errors = None
    client.load_table_from_json.return_value = load_job
    client.query.side_effect = Exception("merge exploded")

    rows = [{"ticker": "PKO", "snapshot_date": "2026-06-27"}]

    with patch("db.bigquery._get_client", return_value=client):
        with pytest.raises(BigQueryError, match="MERGE failed"):
            merge_company_daily_stats(rows)

    client.delete_table.assert_called_once()
    assert "_tmp_" in client.delete_table.call_args.args[0]
    assert client.delete_table.call_args.kwargs.get("not_found_ok") is True


def test_get_latest_company_stats_fetched_at_returns_isoformat_string():
    """get_latest_company_stats_fetched_at must return the fetched_at isoformat for the date."""
    from datetime import datetime, timezone

    mock_dt = datetime(2026, 6, 27, 9, 1, 5, tzinfo=timezone.utc)

    with patch("db.bigquery._get_client", return_value=_mock_bq_client_with_rows([{"fetched_at": mock_dt}])) as mock_get:
        result = get_latest_company_stats_fetched_at(date(2026, 6, 27))

    assert result == mock_dt.isoformat()
    query_str = mock_get.return_value.query.call_args[0][0]
    assert "company_daily_stats" in query_str
    assert "LIMIT 1" in query_str


def test_get_latest_company_stats_fetched_at_returns_none_when_no_rows():
    with patch("db.bigquery._get_client", return_value=_mock_bq_client_with_rows([])):
        result = get_latest_company_stats_fetched_at(date(2026, 6, 27))
    assert result is None


def test_get_latest_company_stats_fetched_at_raises_on_bq_failure():
    from src.exceptions import BigQueryError

    client = MagicMock()
    client.project = "test-project"
    client.query.side_effect = Exception("bq down")

    with patch("db.bigquery._get_client", return_value=client):
        with pytest.raises(BigQueryError, match="get_latest_company_stats_fetched_at failed"):
            get_latest_company_stats_fetched_at(date(2026, 6, 27))


def test_list_companies_with_hop_info_raises_bigquery_error_on_failure():
    """list_companies_with_hop_info must raise BigQueryError when the BQ query raises."""
    from src.exceptions import BigQueryError

    client = MagicMock()
    client.project = "test-project"
    client.query.side_effect = Exception("simulated BQ failure")

    with patch("db.bigquery._get_client", return_value=client):
        with pytest.raises(BigQueryError, match="list_companies_with_hop_info failed"):
            list_companies_with_hop_info()


# ── get_portfolio_calendar_data (PUL-59) ─────────────────────────────────────

def test_get_portfolio_calendar_data_returns_correct_shape():
    """Returns list[dict] with expected keys for a month with trading-day rows."""
    from datetime import timedelta
    from db.bigquery import get_portfolio_calendar_data

    bq_rows = [
        {"snapshot_date": date(2026, 6, 2), "portfolio_value": 10500.0, "prices_found": 3, "total_positions": 3},
        {"snapshot_date": date(2026, 6, 3), "portfolio_value": 10650.0, "prices_found": 3, "total_positions": 3},
    ]
    with patch("db.bigquery._get_client", return_value=_mock_bq_client_with_rows(bq_rows)):
        result = get_portfolio_calendar_data("port-123", "user-abc", 2026, 6)

    assert isinstance(result, list)
    assert len(result) == 2
    row = result[0]
    assert row["snapshot_date"] == date(2026, 6, 2)
    assert row["portfolio_value"] == 10500.0
    assert row["prices_found"] == 3
    assert row["total_positions"] == 3


def test_get_portfolio_calendar_data_uses_correct_date_params():
    """lookback_start must be month_start − 35 days; end_date must be last day of month."""
    from db.bigquery import get_portfolio_calendar_data
    from google.cloud import bigquery as bq_module

    with patch("db.bigquery._get_client", return_value=_mock_bq_client_with_rows([])) as mock_get:
        get_portfolio_calendar_data("port-abc", "user-xyz", 2026, 6)

    job_config = mock_get.return_value.query.call_args[1]["job_config"]
    params_by_name = {p.name: p for p in job_config.query_parameters}

    assert params_by_name["portfolio_id"].value == "port-abc"
    assert params_by_name["user_id"].value == "user-xyz"
    assert params_by_name["lookback_start"].value == date(2026, 6, 1) - __import__("datetime").timedelta(days=35)
    assert params_by_name["end_date"].value == date(2026, 6, 30)
    assert params_by_name["lookback_start"].type_ == "DATE"
    assert params_by_name["end_date"].type_ == "DATE"


def test_get_portfolio_calendar_data_returns_empty_list_when_no_positions():
    """Returns [] when portfolio has no positions (BQ query returns 0 rows)."""
    from db.bigquery import get_portfolio_calendar_data

    with patch("db.bigquery._get_client", return_value=_mock_bq_client_with_rows([])):
        result = get_portfolio_calendar_data("empty-port", "user-1", 2026, 6)

    assert result == []


def test_get_portfolio_calendar_data_raises_bigquery_error_on_failure():
    """Raises BigQueryError when the BQ query throws."""
    from src.exceptions import BigQueryError
    from db.bigquery import get_portfolio_calendar_data

    client = MagicMock()
    client.project = "test-project"
    client.query.side_effect = Exception("network timeout")

    with patch("db.bigquery._get_client", return_value=client):
        with pytest.raises(BigQueryError, match="get_portfolio_calendar_data failed"):
            get_portfolio_calendar_data("port-123", "user-abc", 2026, 6)
