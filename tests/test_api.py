from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api import create_app

_ADMIN_KEY = "test-admin-key"
_USER_KEY = "test-user-key"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", _ADMIN_KEY)
    monkeypatch.setenv("USER_API_KEY", _USER_KEY)


@pytest.fixture(autouse=True)
def _clear_caches():
    import src.api as m
    m._AC_CACHE.clear()
    m._PERF_CACHE.clear()
    yield
    m._AC_CACHE.clear()
    m._PERF_CACHE.clear()


@pytest.fixture
def api_client(_env):
    return TestClient(create_app())


def test_health_no_auth_returns_200(api_client):
    r = api_client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_auth_role_admin_key_returns_admin(api_client):
    r = api_client.get("/auth/role", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 200
    assert r.json() == {"role": "admin"}


def test_auth_role_invalid_key_returns_401(api_client):
    r = api_client.get("/auth/role", headers={"X-API-Key": "bad-key"})
    assert r.status_code == 401


def test_announcements_admin_returns_list(api_client):
    mock_rows = [{"announcement_id": "abc", "ticker": "PKO", "title": "T",
                  "company": "C", "url": "u", "published_at": "2024-01-01T00:00:00",
                  "post_text": None, "posted_at": None, "x_post_id": None,
                  "analyzed_at": None,
                  "supervisor_attempts": None, "parsed_content": None,
                  "priority": None, "structured_analysis": '{"summary_pl": "test", "sentiment": "pozytywny"}',
                  "analysis_approved": True, "analysis_reject_reason": None,
                  "event_type": "ESPI", "analysis_score": 0.9}]
    with patch("src.api.list_announcements_admin", return_value=mock_rows):
        r = api_client.get("/announcements", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1 and data[0]["ticker"] == "PKO"
    assert isinstance(data[0]["structured_analysis"], dict)
    assert data[0]["structured_analysis"]["summary_pl"] == "test"
    assert data[0]["structured_analysis"]["sentiment"] == "pozytywny"
    assert data[0]["analysis_score"] == 0.9


def test_announcements_user_parses_structured_analysis(api_client):
    mock_rows = [{"company": "PKO", "ticker": "PKO", "event_type": "ESPI",
                  "structured_analysis": '{"summary_pl": "test", "sentiment": "pozytywny"}',
                  "analysis_score": 0.8, "published_at": "2024-01-01T00:00:00"}]
    with patch("src.api.list_announcements_user", return_value=mock_rows):
        r = api_client.get("/announcements", headers={"X-API-Key": _USER_KEY})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data[0]["structured_analysis"], dict)
    assert data[0]["structured_analysis"]["summary_pl"] == "test"
    assert "sentiment" not in data[0]["structured_analysis"]


def test_auth_role_user_key_returns_user(api_client):
    r = api_client.get("/auth/role", headers={"X-API-Key": _USER_KEY})
    assert r.status_code == 200
    assert r.json() == {"role": "user"}


def test_auth_role_missing_key_returns_401(api_client):
    r = api_client.get("/auth/role")
    assert r.status_code == 401


def test_announcements_user_returns_subset_fields(api_client):
    mock_rows = [{"company": "PKO", "ticker": "PKO", "event_type": "ESPI",
                  "structured_analysis": None,
                  "published_at": "2024-01-01T00:00:00"}]
    with patch("src.api.list_announcements_user", return_value=mock_rows):
        r = api_client.get("/announcements", headers={"X-API-Key": _USER_KEY})
    assert r.status_code == 200
    data = r.json()
    assert set(data[0].keys()) == {
        "company", "ticker", "event_type", "structured_analysis", "published_at",
    }


def test_announcements_no_key_returns_401(api_client):
    r = api_client.get("/announcements")
    assert r.status_code == 401


def test_announcements_bq_error_returns_500(api_client):
    from src.exceptions import BigQueryError
    with patch("src.api.list_announcements_admin", side_effect=BigQueryError("boom")):
        r = api_client.get("/announcements", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 500


def test_announcements_filter_ticker_passed_to_bq(api_client):
    with patch("src.api.list_announcements_admin", return_value=[]) as mock_fn:
        api_client.get("/announcements?ticker=CDR", headers={"X-API-Key": _ADMIN_KEY})
    mock_fn.assert_called_once()
    assert mock_fn.call_args.kwargs.get("ticker") == "CDR"


def test_admin_x_posts_admin_returns_list(api_client):
    mock_rows = [{"x_post_id": "p1", "window": "ranek", "post_text": "t1\n\nt2",
                  "tweet_ids": "1,2", "posted_at": "2026-06-19T06:00:00",
                  "supervisor_attempts": 1, "x_publish_status": "published"}]
    with patch("src.api.list_x_posts_admin", return_value=mock_rows):
        r = api_client.get("/admin/x-posts", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1 and data[0]["x_post_id"] == "p1"


def test_admin_x_posts_user_returns_403(api_client):
    r = api_client.get("/admin/x-posts", headers={"X-API-Key": _USER_KEY})
    assert r.status_code == 403


def test_admin_x_posts_no_key_returns_401(api_client):
    r = api_client.get("/admin/x-posts")
    assert r.status_code == 401


def test_admin_x_posts_bq_error_returns_500(api_client):
    from src.exceptions import BigQueryError
    with patch("src.api.list_x_posts_admin", side_effect=BigQueryError("boom")):
        r = api_client.get("/admin/x-posts", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 500


def test_admin_x_posts_filters_passed_to_bq(api_client):
    with patch("src.api.list_x_posts_admin", return_value=[]) as mock_fn:
        api_client.get(
            "/admin/x-posts?window=ranek&x_publish_status=published&post_text=PASSUS"
            "&from=2026-06-01T00:00:00&to=2026-06-19T00:00:00",
            headers={"X-API-Key": _ADMIN_KEY},
        )
    mock_fn.assert_called_once()
    assert mock_fn.call_args.kwargs.get("window") == "ranek"
    assert mock_fn.call_args.kwargs.get("x_publish_status") == "published"
    assert mock_fn.call_args.kwargs.get("post_text") == "PASSUS"


def test_delete_admin_returns_204(api_client):
    with patch("src.api.delete_announcement", return_value=None):
        r = api_client.delete("/announcements/some-id",
                              headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 204


def test_delete_user_returns_403(api_client):
    r = api_client.delete("/announcements/some-id",
                          headers={"X-API-Key": _USER_KEY})
    assert r.status_code == 403


def test_delete_no_key_returns_401(api_client):
    r = api_client.delete("/announcements/some-id")
    assert r.status_code == 401


def test_delete_not_found_returns_404(api_client):
    from src.exceptions import BigQueryError
    with patch("src.api.delete_announcement",
               side_effect=BigQueryError("delete_announcement: no row matched announcement_id='x'")):
        r = api_client.delete("/announcements/x", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 404


def test_delete_bq_error_returns_500(api_client):
    from src.exceptions import BigQueryError
    with patch("src.api.delete_announcement",
               side_effect=BigQueryError("connection failed")):
        r = api_client.delete("/announcements/x", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 500


def test_announcements_page_and_page_size_passed_to_bq(api_client):
    with patch("src.api.list_announcements_admin", return_value=[]) as mock_fn:
        api_client.get("/announcements?page=2&page_size=50", headers={"X-API-Key": _ADMIN_KEY})
    mock_fn.assert_called_once()
    assert mock_fn.call_args.kwargs.get("page") == 2
    assert mock_fn.call_args.kwargs.get("page_size") == 50


def test_announcements_page_size_out_of_range_returns_422(api_client):
    r = api_client.get("/announcements?page_size=200", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 422


def test_announcements_limit_param_removed_returns_422(api_client):
    r = api_client.get("/announcements?limit=10", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 422


# ── autocomplete endpoints (PUL-25 panel-ui-redesign) ────────────────────────

def test_autocomplete_tickers_valid_key_returns_200(api_client):
    with patch("src.api.list_distinct_tickers", return_value=["CDR", "PKO", "XTB"]):
        r = api_client.get("/autocomplete/tickers", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 200
    assert r.json() == ["CDR", "PKO", "XTB"]


def test_autocomplete_tickers_no_key_returns_401(api_client):
    r = api_client.get("/autocomplete/tickers")
    assert r.status_code == 401


def test_autocomplete_companies_valid_key_returns_200(api_client):
    with patch("src.api.list_distinct_companies", return_value=["Alior Bank SA", "PKO Bank Polski SA"]):
        r = api_client.get("/autocomplete/companies", headers={"X-API-Key": _USER_KEY})
    assert r.status_code == 200
    assert r.json() == ["Alior Bank SA", "PKO Bank Polski SA"]


def test_autocomplete_companies_no_key_returns_401(api_client):
    r = api_client.get("/autocomplete/companies")
    assert r.status_code == 401


def test_autocomplete_tickers_bq_error_returns_500(api_client):
    from src.exceptions import BigQueryError
    with patch("src.api.list_distinct_tickers", side_effect=BigQueryError("bq down")):
        r = api_client.get("/autocomplete/tickers", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 500


def test_autocomplete_tickers_cache_hit_skips_bq(api_client):
    """Second call within TTL must return cached result without calling BQ again."""
    with patch("src.api.list_distinct_tickers", return_value=["PKO"]) as mock_bq:
        api_client.get("/autocomplete/tickers", headers={"X-API-Key": _ADMIN_KEY})
        api_client.get("/autocomplete/tickers", headers={"X-API-Key": _ADMIN_KEY})
    mock_bq.assert_called_once()


def test_autocomplete_companies_bq_error_returns_500(api_client):
    from src.exceptions import BigQueryError
    with patch("src.api.list_distinct_companies", side_effect=BigQueryError("bq down")):
        r = api_client.get("/autocomplete/companies", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 500


def test_autocomplete_companies_cache_hit_skips_bq(api_client):
    """Second call within TTL must return cached result without calling BQ again."""
    with patch("src.api.list_distinct_companies", return_value=["Alior Bank SA"]) as mock_bq:
        api_client.get("/autocomplete/companies", headers={"X-API-Key": _ADMIN_KEY})
        api_client.get("/autocomplete/companies", headers={"X-API-Key": _ADMIN_KEY})
    mock_bq.assert_called_once()


# ── admin treemap endpoint (PUL-45 admin-ui-portfolio-treemap, PUL-50 multi-wallet) ─

_LATEST_SNAPSHOT_MAIN = {
    "snapshot_id": "snap1", "wallet": "main", "snapshot_date": "2026-06-19",
    "total_value": 5000.0, "currency": "PLN",
    "day_change_abs": 10.0, "day_change_pct": 0.2,
    "positions_json": '{"positions": [{"ticker": "PKO", "value": 1100.0, "pct": 22.0}], "media_attached": false}',
}
_PRIOR_SNAPSHOT_MAIN = {
    "snapshot_id": "snap0", "wallet": "main", "snapshot_date": "2026-06-18",
    "total_value": 4900.0, "currency": "PLN",
    "day_change_abs": 0.0, "day_change_pct": 0.0,
    "positions_json": '{"positions": [{"ticker": "PKO", "value": 1000.0, "pct": 20.0}], "media_attached": false}',
}
_LATEST_SNAPSHOT_IKZE = {
    "snapshot_id": "snap2", "wallet": "ikze", "snapshot_date": "2026-06-19",
    "total_value": 2000.0, "currency": "PLN",
    "day_change_abs": 5.0, "day_change_pct": 0.5,
    "positions_json": '{"positions": [{"ticker": "CDR", "value": 800.0, "pct": 40.0}], "media_attached": false}',
}


def _snapshot_side_effect(latest_by_wallet: dict):
    def _fn(wallet):
        return latest_by_wallet.get(wallet)
    return _fn


def test_admin_treemap_admin_returns_both_wallets_keyed_with_deltas(api_client):
    with (
        patch(
            "src.api.get_latest_snapshot_for_wallet",
            side_effect=_snapshot_side_effect(
                {"main": _LATEST_SNAPSHOT_MAIN, "ikze": _LATEST_SNAPSHOT_IKZE}
            ),
        ),
        patch("src.api.get_latest_snapshot_before", return_value=_PRIOR_SNAPSHOT_MAIN),
        patch("src.api.get_latest_company_stats_fetched_at", return_value="2026-06-19T09:01:05+00:00"),
    ):
        r = api_client.get("/admin/portfolio/treemap", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 200
    body = r.json()
    assert list(body.keys()) == ["main", "ikze", "as_of", "stats_fetched_at"]
    assert body["as_of"] == "2026-06-19"
    assert body["stats_fetched_at"] == "2026-06-19T09:01:05+00:00"
    assert body["main"] == [
        pytest.approx({
            "ticker": "PKO",
            "position_value_pln": 1100.0,
            "daily_change_pln": 100.0,
            "daily_change_pct": 10.0,
            "portfolio_share_pct": 22.0,
            "since_purchase_pct": 22.0,
            "since_purchase_pln": 198.36065573770486,
        })
    ]
    assert body["ikze"] == [
        pytest.approx({
            "ticker": "CDR",
            "position_value_pln": 800.0,
            "daily_change_pln": None,
            "daily_change_pct": None,
            "portfolio_share_pct": 40.0,
            "since_purchase_pct": 40.0,
            "since_purchase_pln": 228.57142857142856,
        })
    ]


def test_admin_treemap_one_wallet_missing_other_still_renders(api_client):
    with (
        patch(
            "src.api.get_latest_snapshot_for_wallet",
            side_effect=_snapshot_side_effect({"main": _LATEST_SNAPSHOT_MAIN}),
        ),
        patch("src.api.get_latest_snapshot_before", return_value=_PRIOR_SNAPSHOT_MAIN),
        patch("src.api.get_latest_company_stats_fetched_at", return_value=None),
    ):
        r = api_client.get("/admin/portfolio/treemap", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 200
    body = r.json()
    assert body["ikze"] == []
    assert len(body["main"]) == 1


def test_admin_treemap_no_snapshots_returns_empty_lists_for_both_wallets(api_client):
    with patch("src.api.get_latest_snapshot_for_wallet", return_value=None):
        r = api_client.get("/admin/portfolio/treemap", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 200
    assert r.json() == {"main": [], "ikze": [], "as_of": None, "stats_fetched_at": None}


def test_admin_treemap_user_returns_403(api_client):
    r = api_client.get("/admin/portfolio/treemap", headers={"X-API-Key": _USER_KEY})
    assert r.status_code == 403


def test_admin_treemap_no_key_returns_401(api_client):
    r = api_client.get("/admin/portfolio/treemap")
    assert r.status_code == 401


def test_admin_treemap_bq_error_returns_500(api_client):
    from src.exceptions import BigQueryError
    with patch("src.api.get_latest_snapshot_for_wallet", side_effect=BigQueryError("boom")):
        r = api_client.get("/admin/portfolio/treemap", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 500


def test_admin_treemap_first_wallet_succeeds_second_raises_returns_500(api_client):
    """`main`'s already-computed data is discarded, not partially returned, when `ikze` raises."""
    from src.exceptions import BigQueryError

    def _side_effect(wallet):
        if wallet == "main":
            return _LATEST_SNAPSHOT_MAIN
        raise BigQueryError("boom")

    with (
        patch("src.api.get_latest_snapshot_for_wallet", side_effect=_side_effect),
        patch("src.api.get_latest_snapshot_before", return_value=_PRIOR_SNAPSHOT_MAIN),
    ):
        r = api_client.get("/admin/portfolio/treemap", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 500


def test_admin_treemap_malformed_position_value_returns_500(api_client):
    malformed_snapshot = {
        **_LATEST_SNAPSHOT_MAIN,
        "positions_json": '{"positions": [{"ticker": "PKO", "value": "not-a-number"}], "media_attached": false}',
    }
    with (
        patch(
            "src.api.get_latest_snapshot_for_wallet",
            side_effect=_snapshot_side_effect({"main": malformed_snapshot}),
        ),
        patch("src.api.get_latest_snapshot_before", return_value=None),
    ):
        r = api_client.get("/admin/portfolio/treemap", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 500


def test_static_treemap_layout_js_is_reachable(api_client):
    r = api_client.get("/static/js/treemap-layout.js")
    assert r.status_code == 200


def test_root_route_still_serves_html_after_static_mount(api_client):
    r = api_client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


# ── watchlist endpoints (PUL-28 my-wallet-watchlist) ──────────────────────────

_CLIENT_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def user_client(api_client, jwt_env):
    """Client with a valid user session cookie — PUL-74: per-user endpoints are JWT-only."""
    api_client.cookies.set("session", _make_session_token(user_id=_CLIENT_ID))
    return api_client


@pytest.fixture
def admin_client(api_client, jwt_env):
    """Client with a valid admin session cookie (role claim from PUL-83)."""
    api_client.cookies.set("session", _make_session_token(user_id=_CLIENT_ID, role="admin"))
    return api_client


def test_get_watchlist_api_key_only_returns_401(api_client):
    r = api_client.get("/watchlist", headers={"X-API-Key": _USER_KEY})
    assert r.status_code == 401


def test_get_watchlist_returns_tickers(user_client):
    with patch("src.api.list_watchlist_tickers", return_value=["PKO", "CDR"]):
        r = user_client.get(
            "/watchlist"
        )
    assert r.status_code == 200
    assert r.json() == {"tickers": ["PKO", "CDR"]}


def test_post_watchlist_api_key_only_returns_401(api_client):
    r = api_client.post("/watchlist/PKO", headers={"X-API-Key": _USER_KEY})
    assert r.status_code == 401


def test_post_watchlist_unknown_ticker_returns_422(user_client):
    with patch("src.api.list_distinct_tickers", return_value=["PKO", "CDR"]):
        r = user_client.post(
            "/watchlist/NOPE"
        )
    assert r.status_code == 422


def test_post_watchlist_known_ticker_returns_200(user_client):
    with (
        patch("src.api.list_distinct_tickers", return_value=["PKO", "CDR"]),
        patch("src.api.add_watchlist_ticker", return_value=None) as mock_add,
    ):
        r = user_client.post(
            "/watchlist/PKO"
        )
    assert r.status_code == 200
    assert r.json() == {"ticker": "PKO", "added": True}
    mock_add.assert_called_once_with(_CLIENT_ID, "PKO")


def test_post_watchlist_duplicate_add_is_no_op(user_client):
    with (
        patch("src.api.list_distinct_tickers", return_value=["PKO"]),
        patch("src.api.add_watchlist_ticker", return_value=None) as mock_add,
    ):
        user_client.post("/watchlist/PKO")
        r = user_client.post(
            "/watchlist/PKO"
        )
    assert r.status_code == 200
    assert mock_add.call_count == 2


def test_delete_watchlist_api_key_only_returns_401(api_client):
    r = api_client.delete("/watchlist/PKO", headers={"X-API-Key": _USER_KEY})
    assert r.status_code == 401


def test_delete_watchlist_nonexistent_ticker_returns_204(user_client):
    with patch("src.api.remove_watchlist_ticker", return_value=None) as mock_remove:
        r = user_client.delete(
            "/watchlist/NEVERADDED"
        )
    assert r.status_code == 204
    mock_remove.assert_called_once_with(_CLIENT_ID, "NEVERADDED")


def test_watchlist_bq_error_returns_500(user_client):
    from src.exceptions import BigQueryError
    with patch("src.api.list_watchlist_tickers", side_effect=BigQueryError("boom")):
        r = user_client.get(
            "/watchlist"
        )
    assert r.status_code == 500


def test_announcements_my_wallet_api_key_only_returns_401(api_client):
    r = api_client.get("/announcements/my-wallet", headers={"X-API-Key": _USER_KEY})
    assert r.status_code == 401


def test_announcements_my_wallet_returns_filtered_announcements(user_client):
    mock_rows = [{"company": "PKO", "ticker": "PKO", "event_type": "ESPI",
                  "structured_analysis": None, "published_at": "2024-01-01T00:00:00"}]
    with patch("src.api.list_announcements_for_watchlist", return_value=mock_rows) as mock_fn:
        r = user_client.get(
            "/announcements/my-wallet",
        )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1 and data[0]["ticker"] == "PKO"
    mock_fn.assert_called_once()
    assert mock_fn.call_args.args[0] == _CLIENT_ID


def test_announcements_my_wallet_bq_error_returns_500(user_client):
    from src.exceptions import BigQueryError
    with patch("src.api.list_announcements_for_watchlist", side_effect=BigQueryError("boom")):
        r = user_client.get(
            "/announcements/my-wallet",
        )
    assert r.status_code == 500


_MY_WALLET_ROW_WITH_ANALYSIS = {
    "company": "PKO", "ticker": "PKO", "event_type": "wyniki_finansowe",
    "structured_analysis": '{"summary_pl": "ok", "sentiment": "pozytywny"}',
    "published_at": "2024-01-01T00:00:00", "analysis_score": 85.0,
}


def test_announcements_my_wallet_admin_gets_sentiment_and_score(admin_client):
    with patch(
        "src.api.list_announcements_for_watchlist",
        return_value=[dict(_MY_WALLET_ROW_WITH_ANALYSIS)],
    ):
        r = admin_client.get("/announcements/my-wallet")
    assert r.status_code == 200
    data = r.json()
    assert data[0]["analysis_score"] == 85.0
    assert data[0]["structured_analysis"]["sentiment"] == "pozytywny"


def test_announcements_my_wallet_user_never_gets_sentiment_or_score(user_client):
    with patch(
        "src.api.list_announcements_for_watchlist",
        return_value=[dict(_MY_WALLET_ROW_WITH_ANALYSIS)],
    ):
        r = user_client.get(
            "/announcements/my-wallet",
        )
    assert r.status_code == 200
    data = r.json()
    assert "analysis_score" not in data[0]
    assert "sentiment" not in data[0]["structured_analysis"]


def test_list_announcements_for_watchlist_query_selects_analysis_score():
    """Regression (lessons.md): mocked BQ tests don't parse SQL — assert the query string."""
    from unittest.mock import MagicMock
    from db import bigquery as bq

    captured = {}

    def _capture(query, job_config=None):
        captured["query"] = query
        job = MagicMock()
        job.result.return_value = []
        return job

    client = MagicMock()
    client.query.side_effect = _capture
    with patch.object(bq, "_get_client", return_value=client):
        bq.list_announcements_for_watchlist("client-1")
    assert "a.analysis_score" in captured["query"]


# ── watchlist sentiment summary endpoint (PUL-87) ─────────────────────────────

_FAKE_SENTIMENT_SUMMARY = {
    "counts": {"pozytywny": 3, "neutralny": 5, "negatywny": 1},
    "avg_score": 72,
    "days_with_data": 4,
    "window_from": "2026-07-14T00:00:00+00:00",
    "window_to": "2026-07-21T00:00:00+00:00",
    "total": 9,
}


def test_sentiment_summary_user_forbidden(user_client):
    """PUL-87/PUL-82: sentiment/score are admin-only — the summary endpoint must
    403 a regular user, not merely strip fields."""
    r = user_client.get("/announcements/my-wallet/sentiment-summary")
    assert r.status_code == 403


def test_sentiment_summary_admin_returns_shape(admin_client):
    with patch("src.api.summarize_watchlist_sentiment", return_value=dict(_FAKE_SENTIMENT_SUMMARY)):
        r = admin_client.get("/announcements/my-wallet/sentiment-summary")
    assert r.status_code == 200
    data = r.json()
    assert data["counts"] == {"pozytywny": 3, "neutralny": 5, "negatywny": 1}
    assert data["avg_score"] == 72
    assert data["days_with_data"] == 4
    assert data["total"] == 9
    assert data["window_from"] and data["window_to"]


def test_summarize_watchlist_sentiment_query_normalizes_and_windows():
    """Structural lock (plan-review F1/F4): BQ is mocked so we can't execute the
    fold — assert the query interpolates the shared normalization (EN→PL) and the
    f-string 7-day window, and never binds the interval as a parameter."""
    from unittest.mock import MagicMock
    from db import bigquery as bq

    captured = {}

    def _capture(query, job_config=None):
        captured["query"] = query
        job = MagicMock()
        job.result.return_value = []
        return job

    client = MagicMock()
    client.query.side_effect = _capture
    with patch.object(bq, "_get_client", return_value=client):
        bq.summarize_watchlist_sentiment("client-1")

    q = captured["query"]
    assert "JSON_VALUE(a.structured_analysis" in q
    assert "positive" in q and "negative" in q  # English drift folded to PL
    assert "INTERVAL 7 DAY" in q                 # f-string window, per db-bigquery pattern
    assert "INTERVAL @" not in q                 # never parameterize the interval


# ── watchlist sentiment drill-down endpoint (PUL-87) ──────────────────────────


def test_sentiment_drilldown_user_forbidden(user_client):
    r = user_client.get("/announcements/my-wallet/sentiment/pozytywny")
    assert r.status_code == 403


def test_sentiment_drilldown_invalid_bucket_422(admin_client):
    r = admin_client.get("/announcements/my-wallet/sentiment/euphoric")
    assert r.status_code == 422


def test_sentiment_drilldown_admin_returns_list(admin_client):
    with patch(
        "src.api.list_watchlist_by_sentiment",
        return_value=[dict(_MY_WALLET_ROW_WITH_ANALYSIS)],
    ):
        r = admin_client.get("/announcements/my-wallet/sentiment/pozytywny")
    assert r.status_code == 200
    data = r.json()
    assert data["truncated"] is False
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["ticker"] == "PKO"
    assert item["analysis_score"] == 85.0
    assert item["structured_analysis"]["sentiment"] == "pozytywny"


def test_list_watchlist_by_sentiment_query_shares_normalization():
    """Structural consistency lock (plan-review F4): the drill-down must embed the
    SAME normalization fragment as the summary, filter WHERE bucket = @bucket, use
    the f-string window, and bound the result — so popup contents match bar counts."""
    from unittest.mock import MagicMock
    from db import bigquery as bq

    captured = {}

    def _capture(query, job_config=None):
        captured["query"] = query
        job = MagicMock()
        job.result.return_value = []
        return job

    client = MagicMock()
    client.query.side_effect = _capture
    with patch.object(bq, "_get_client", return_value=client):
        bq.list_watchlist_by_sentiment("client-1", "pozytywny")

    q = captured["query"]
    assert bq._SENTIMENT_BUCKET_SQL in q       # identical fragment as the summary
    assert "= @bucket" in q                      # filter on the requested bucket
    assert "INTERVAL 7 DAY" in q and "INTERVAL @" not in q
    assert "LIMIT @limit" in q                    # bounded result set


# ── public top-announcements endpoint (PUL-72) ────────────────────────────────

_PUBLIC_TOP_ROWS = [
    {"company": "PKO SA", "ticker": "PKO", "title": "Wyniki Q2 2026",
     "event_type": "wyniki", "published_at": "2026-07-01T00:00:00",
     "structured_analysis": '{"summary_pl": "Rekordowe wyniki kwartalne", "sentiment": "pozytywny"}'},
    {"company": "CD Projekt SA", "ticker": "CDR", "title": "Nowa gra",
     "event_type": "umowa", "published_at": "2026-07-02T00:00:00",
     "structured_analysis": None},
]


def test_public_top_announcements_no_auth_returns_200(api_client):
    with patch("src.api.list_top_announcements_public", return_value=_PUBLIC_TOP_ROWS):
        r = api_client.get("/api/public/top-announcements")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_public_top_announcements_field_set_has_no_score_or_sentiment(api_client):
    with patch("src.api.list_top_announcements_public", return_value=_PUBLIC_TOP_ROWS):
        r = api_client.get("/api/public/top-announcements")
    data = r.json()
    assert set(data[0].keys()) == {
        "company", "ticker", "title", "event_type", "published_at", "summary",
    }
    assert data[0]["summary"] == "Rekordowe wyniki kwartalne"
    assert data[1]["summary"] is None
    # Containment guard: neither the score nor the raw analysis JSON may leak.
    assert "analysis_score" not in r.text
    assert "sentiment" not in r.text


def test_public_top_announcements_cache_hit_skips_bq(api_client):
    with patch("src.api.list_top_announcements_public", return_value=_PUBLIC_TOP_ROWS) as mock_fn:
        r1 = api_client.get("/api/public/top-announcements")
        r2 = api_client.get("/api/public/top-announcements")
    assert r1.status_code == 200 and r2.status_code == 200
    assert mock_fn.call_count == 1


def test_public_top_announcements_bq_error_serves_empty_and_negative_caches(api_client):
    from src.exceptions import BigQueryError
    with patch("src.api.list_top_announcements_public", side_effect=BigQueryError("secret-detail")) as mock_fn:
        r1 = api_client.get("/api/public/top-announcements")
        r2 = api_client.get("/api/public/top-announcements")
    assert r1.status_code == 200 and r1.json() == []
    assert "secret-detail" not in r1.text
    # Negative cache: the failing BQ fn is not re-called within the TTL.
    assert r2.json() == []
    assert mock_fn.call_count == 1


def test_list_top_announcements_public_query_orders_by_score_without_selecting_it():
    """Regression (lessons.md): mocked BQ tests don't parse SQL — assert the query string.
    Score containment: analysis_score orders the query but must not be selected."""
    from unittest.mock import MagicMock
    from db import bigquery as bq

    captured = {}

    def _capture(query, job_config=None):
        captured["query"] = query
        job = MagicMock()
        job.result.return_value = []
        return job

    client = MagicMock()
    client.query.side_effect = _capture
    with patch.object(bq, "_get_client", return_value=client):
        bq.list_top_announcements_public()
    q = captured["query"]
    assert "ORDER BY analysis_score DESC, published_at DESC" in q
    assert "analysis_approved = TRUE" in q
    assert "analysis_score IS NOT NULL" in q
    select_clause = q.split("FROM")[0]
    assert "analysis_score" not in select_clause


# ── portfolio positions endpoints (PUL-65) ────────────────────────────────────

_POSITION_WITH_PRICE = {
    "ticker": "PKO",
    "company_name": "PKO Bank Polski SA",
    "shares": 10.0,
    "avg_buy_price": 40.0,
    "current_price": 52.0,
    "daily_change_pct": 1.5,
    "price_as_of": "2026-06-27",
}

_POSITION_NO_PRICE = {
    "ticker": "XYZ",
    "company_name": "Firma XYZ",
    "shares": 5.0,
    "avg_buy_price": 30.0,
    "current_price": None,
    "daily_change_pct": None,
    "price_as_of": None,
}


def test_get_portfolio_positions_returns_empty_list(user_client):
    with (
        patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]),
        patch("src.api.list_user_portfolio_positions", return_value=[]),
    ):
        r = user_client.get(
            f"/api/portfolio/positions?portfolio_id={_WALLET_ID}",
        )
    assert r.status_code == 200
    assert r.json() == []


def test_get_portfolio_positions_with_price_computes_pnl(user_client):
    with (
        patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]),
        patch("src.api.list_user_portfolio_positions", return_value=[_POSITION_WITH_PRICE]),
    ):
        r = user_client.get(
            f"/api/portfolio/positions?portfolio_id={_WALLET_ID}",
        )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    pos = data[0]
    assert pos["ticker"] == "PKO"
    assert pos["shares"] == 10.0
    assert pos["current_price"] == 52.0
    assert pos["pnl_pln"] == pytest.approx((52.0 - 40.0) * 10.0)
    assert pos["pnl_pct"] == pytest.approx((52.0 - 40.0) / 40.0 * 100)


def test_get_portfolio_positions_without_price_has_null_pnl(user_client):
    with (
        patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]),
        patch("src.api.list_user_portfolio_positions", return_value=[_POSITION_NO_PRICE]),
    ):
        r = user_client.get(
            f"/api/portfolio/positions?portfolio_id={_WALLET_ID}",
        )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    pos = data[0]
    assert pos["current_price"] is None
    assert pos["pnl_pln"] is None
    assert pos["pnl_pct"] is None


def test_get_portfolio_positions_api_key_only_returns_401(api_client):
    r = api_client.get("/api/portfolio/positions", headers={"X-API-Key": _USER_KEY})
    assert r.status_code == 401


def test_get_portfolio_positions_no_key_returns_401(api_client):
    r = api_client.get("/api/portfolio/positions")
    assert r.status_code == 401


def test_get_portfolio_positions_bq_error_returns_500(user_client):
    from src.exceptions import BigQueryError
    with (
        patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]),
        patch("src.api.list_user_portfolio_positions", side_effect=BigQueryError("boom")),
    ):
        r = user_client.get(
            f"/api/portfolio/positions?portfolio_id={_WALLET_ID}",
        )
    assert r.status_code == 500


def test_post_portfolio_position_valid_ticker_returns_200(user_client):
    with (
        patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]),
        patch("src.api.list_distinct_portfolio_tickers", return_value=["PKO", "CDR"]),
        patch("src.api.upsert_user_portfolio_position", return_value=None) as mock_upsert,
    ):
        r = user_client.post(
            "/api/portfolio/positions",
            json={"portfolio_id": _WALLET_ID, "ticker": "PKO",
                  "company_name": "PKO Bank Polski SA",
                  "shares": 10.0, "avg_buy_price": 40.0},
        )
    assert r.status_code == 200
    assert r.json() == {"ticker": "PKO", "upserted": True}
    mock_upsert.assert_called_once_with(_CLIENT_ID, _WALLET_ID, "PKO", "PKO Bank Polski SA", 10.0, 40.0)


def test_post_portfolio_position_unknown_ticker_returns_422(user_client):
    with (
        patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]),
        patch("src.api.list_distinct_portfolio_tickers", return_value=["PKO", "CDR"]),
    ):
        r = user_client.post(
            "/api/portfolio/positions",
            json={"portfolio_id": _WALLET_ID, "ticker": "NOPE",
                  "company_name": "Firma", "shares": 10.0, "avg_buy_price": 40.0},
        )
    assert r.status_code == 422
    assert "Unknown ticker" in r.json()["detail"]


def test_post_portfolio_position_zero_shares_returns_422(user_client):
    r = user_client.post(
        "/api/portfolio/positions",
        json={"ticker": "PKO", "company_name": "PKO Bank Polski SA",
              "shares": 0.0, "avg_buy_price": 40.0},
    )
    assert r.status_code == 422


def test_post_portfolio_position_negative_shares_returns_422(user_client):
    r = user_client.post(
        "/api/portfolio/positions",
        json={"ticker": "PKO", "company_name": "PKO Bank Polski SA",
              "shares": -5.0, "avg_buy_price": 40.0},
    )
    assert r.status_code == 422


def test_post_portfolio_position_api_key_only_returns_401(api_client):
    r = api_client.post(
        "/api/portfolio/positions",
        json={"ticker": "PKO", "company_name": "PKO Bank Polski SA",
              "shares": 10.0, "avg_buy_price": 40.0},
        headers={"X-API-Key": _USER_KEY},
    )
    assert r.status_code == 401


def test_post_portfolio_position_no_key_returns_401(api_client):
    r = api_client.post(
        "/api/portfolio/positions",
        json={"ticker": "PKO", "company_name": "PKO Bank Polski SA",
              "shares": 10.0, "avg_buy_price": 40.0},
    )
    assert r.status_code == 401


def test_post_portfolio_position_bq_error_returns_500(user_client):
    from src.exceptions import BigQueryError
    with (
        patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]),
        patch("src.api.list_distinct_portfolio_tickers", return_value=["PKO"]),
        patch("src.api.upsert_user_portfolio_position", side_effect=BigQueryError("boom")),
    ):
        r = user_client.post(
            "/api/portfolio/positions",
            json={"portfolio_id": _WALLET_ID, "ticker": "PKO",
                  "company_name": "PKO Bank Polski SA",
                  "shares": 10.0, "avg_buy_price": 40.0},
        )
    assert r.status_code == 500


def test_delete_portfolio_position_returns_204(user_client):
    with (
        patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]),
        patch("src.api.delete_user_portfolio_position", return_value=None) as mock_del,
    ):
        r = user_client.delete(
            f"/api/portfolio/positions/PKO?portfolio_id={_WALLET_ID}",
        )
    assert r.status_code == 204
    mock_del.assert_called_once_with(_CLIENT_ID, _WALLET_ID, "PKO")


def test_delete_portfolio_position_api_key_only_returns_401(api_client):
    r = api_client.delete("/api/portfolio/positions/PKO", headers={"X-API-Key": _USER_KEY})
    assert r.status_code == 401


def test_delete_portfolio_position_no_key_returns_401(api_client):
    r = api_client.delete("/api/portfolio/positions/PKO")
    assert r.status_code == 401


def test_delete_portfolio_position_bq_error_returns_500(user_client):
    from src.exceptions import BigQueryError
    with (
        patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]),
        patch("src.api.delete_user_portfolio_position", side_effect=BigQueryError("boom")),
    ):
        r = user_client.delete(
            f"/api/portfolio/positions/PKO?portfolio_id={_WALLET_ID}",
        )
    assert r.status_code == 500


# ── portfolio wallet endpoints (PUL-64 non-admin-portfolio-treemap, Phase 2) ──

_WALLET_ID = "aaaabbbb-0000-1111-2222-ccccddddeeee"

_WALLET_GLOWNY = {
    "portfolio_id": _WALLET_ID,
    "portfolio_type": "glowny",
    "portfolio_name": None,
    "display_order": 1,
    "user_id": _CLIENT_ID,
    "created_at": "2026-01-01T00:00:00+00:00",
}

_WALLET_INNY_1 = {
    "portfolio_id": "inny-0001",
    "portfolio_type": "inny",
    "portfolio_name": "Mój inny",
    "display_order": 4,
    "user_id": _CLIENT_ID,
    "created_at": "2026-01-02T00:00:00+00:00",
}

_WALLET_INNY_2 = {
    "portfolio_id": "inny-0002",
    "portfolio_type": "inny",
    "portfolio_name": "Drugi inny",
    "display_order": 5,
    "user_id": _CLIENT_ID,
    "created_at": "2026-01-03T00:00:00+00:00",
}


def test_get_wallets_returns_list(user_client):
    with patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]):
        r = user_client.get("/api/portfolio/wallets")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["portfolio_type"] == "glowny"
    assert data[0]["portfolio_id"] == _WALLET_ID


def test_get_wallets_no_key_returns_401(api_client):
    r = api_client.get("/api/portfolio/wallets")
    assert r.status_code == 401


def test_post_wallet_glowny_creates_and_assigns_orphans(user_client):
    with (
        patch("src.api.list_user_portfolios", return_value=[]),
        patch("src.api.create_user_portfolio", return_value=_WALLET_ID) as mock_create,
        patch("src.api.assign_orphan_positions_to_portfolio") as mock_assign,
    ):
        r = user_client.post(
            "/api/portfolio/wallets",
            json={"portfolio_type": "glowny"},
        )
    assert r.status_code == 201
    body = r.json()
    assert body["portfolio_id"] == _WALLET_ID
    assert body["portfolio_type"] == "glowny"
    mock_create.assert_called_once_with(_CLIENT_ID, "glowny", None)
    mock_assign.assert_called_once_with(_CLIENT_ID, _WALLET_ID)


def test_post_wallet_duplicate_type_returns_409(user_client):
    with patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]):
        r = user_client.post(
            "/api/portfolio/wallets",
            json={"portfolio_type": "glowny"},
        )
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


def test_post_wallet_third_inny_returns_409(user_client):
    with patch("src.api.list_user_portfolios", return_value=[_WALLET_INNY_1, _WALLET_INNY_2]):
        r = user_client.post(
            "/api/portfolio/wallets",
            json={"portfolio_type": "inny", "portfolio_name": "Trzeci"},
        )
    assert r.status_code == 409
    assert "Maximum 2" in r.json()["detail"]


def test_delete_wallet_own_returns_204(user_client):
    with (
        patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]),
        patch("src.api.delete_user_portfolio") as mock_del,
    ):
        r = user_client.delete(
            f"/api/portfolio/wallets/{_WALLET_ID}",
        )
    assert r.status_code == 204
    mock_del.assert_called_once_with(_CLIENT_ID, _WALLET_ID)


def test_delete_wallet_wrong_user_returns_404(user_client):
    with patch("src.api.list_user_portfolios", return_value=[]):
        r = user_client.delete(
            "/api/portfolio/wallets/nonexistent-id",
        )
    assert r.status_code == 404


def test_post_wallet_bq_error_returns_500(user_client):
    from src.exceptions import BigQueryError
    with (
        patch("src.api.list_user_portfolios", return_value=[]),
        patch("src.api.create_user_portfolio", side_effect=BigQueryError("bq down")),
    ):
        r = user_client.post(
            "/api/portfolio/wallets",
            json={"portfolio_type": "glowny"},
        )
    assert r.status_code == 500


def test_delete_wallet_bq_error_returns_500(user_client):
    from src.exceptions import BigQueryError
    with (
        patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]),
        patch("src.api.delete_user_portfolio", side_effect=BigQueryError("bq down")),
    ):
        r = user_client.delete(
            f"/api/portfolio/wallets/{_WALLET_ID}",
        )
    assert r.status_code == 500


# ── Phase 3: positions CRUD update + treemap endpoint (PUL-64) ──────────────

_POSITION_WITH_PRICE_AS_OF = {
    **_POSITION_WITH_PRICE,
    "price_as_of": "2026-06-27",
}


def test_get_portfolio_positions_without_portfolio_id_returns_422(user_client):
    """portfolio_id is now a required query parameter."""
    with patch("src.api.list_user_portfolio_positions", return_value=[]):
        r = user_client.get(
            "/api/portfolio/positions",
        )
    assert r.status_code == 422


def test_get_portfolio_positions_scoped_to_portfolio(user_client):
    """GET positions with portfolio_id passes it to BQ and validates ownership."""
    with (
        patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]),
        patch("src.api.list_user_portfolio_positions", return_value=[]) as mock_list,
    ):
        r = user_client.get(
            f"/api/portfolio/positions?portfolio_id={_WALLET_ID}",
        )
    assert r.status_code == 200
    mock_list.assert_called_once_with(_CLIENT_ID, _WALLET_ID)


def test_get_portfolio_positions_wrong_portfolio_returns_404(user_client):
    """portfolio_id not owned by user → 404."""
    with patch("src.api.list_user_portfolios", return_value=[]):
        r = user_client.get(
            f"/api/portfolio/positions?portfolio_id={_WALLET_ID}",
        )
    assert r.status_code == 404


def test_delete_portfolio_position_without_portfolio_id_returns_422(user_client):
    """portfolio_id query param is now required for DELETE."""
    with patch("src.api.delete_user_portfolio_position", return_value=None):
        r = user_client.delete(
            "/api/portfolio/positions/PKO",
        )
    assert r.status_code == 422


def test_post_portfolio_position_passes_portfolio_id_to_bq(user_client):
    """POST positions includes portfolio_id in body and passes it to BQ."""
    with (
        patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]),
        patch("src.api.list_distinct_portfolio_tickers", return_value=["PKO"]),
        patch("src.api.upsert_user_portfolio_position", return_value=None) as mock_upsert,
    ):
        r = user_client.post(
            "/api/portfolio/positions",
            json={
                "portfolio_id": _WALLET_ID,
                "ticker": "PKO",
                "company_name": "PKO Bank Polski SA",
                "shares": 10.0,
                "avg_buy_price": 40.0,
            },
        )
    assert r.status_code == 200
    mock_upsert.assert_called_once_with(
        _CLIENT_ID, _WALLET_ID, "PKO", "PKO Bank Polski SA", 10.0, 40.0
    )


def test_get_portfolio_treemap_returns_correct_shape(user_client):
    _computed = [{
        "ticker": "PKO",
        "position_value_pln": 520.0,
        "daily_change_pct": 1.5,
        "daily_change_pln": 7.72,
        "since_purchase_pct": 30.0,
        "since_purchase_pln": 120.0,
        "portfolio_share_pct": 100.0,
    }]
    with (
        patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]),
        patch("src.api.list_user_portfolio_positions", return_value=[_POSITION_WITH_PRICE]),
        patch(
            "src.api.compute_user_portfolio_treemap_positions",
            create=True,
            return_value=_computed,
        ),
    ):
        r = user_client.get("/api/portfolio/treemap")
    assert r.status_code == 200
    data = r.json()
    assert "portfolios" in data
    assert "as_of" in data
    portfolios = data["portfolios"]
    assert len(portfolios) == 1
    assert portfolios[0]["portfolio_id"] == _WALLET_ID
    assert portfolios[0]["portfolio_type"] == "glowny"
    assert len(portfolios[0]["positions"]) == 1
    assert portfolios[0]["positions"][0]["ticker"] == "PKO"


def test_get_portfolio_treemap_zero_portfolios_returns_empty(user_client):
    with patch("src.api.list_user_portfolios", return_value=[]):
        r = user_client.get("/api/portfolio/treemap")
    assert r.status_code == 200
    assert r.json() == {"portfolios": [], "as_of": None}


def test_admin_portfolio_treemap_endpoint_unaffected(api_client):
    """Phase 3 must not break the admin treemap endpoint."""
    with patch("src.api.get_latest_snapshot_for_wallet", return_value=None):
        r = api_client.get(
            "/admin/portfolio/treemap", headers={"X-API-Key": _ADMIN_KEY}
        )
    assert r.status_code == 200


# ── GET /api/portfolio/calendar (PUL-59) ─────────────────────────────────────

_CAL_PORTFOLIO_ID = _WALLET_ID  # reuse existing wallet fixture
_CAL_DAYS = [
    {
        "date": "2026-06-02", "day": 2, "weekday": 1, "state": "data",
        "portfolio_value": 10000.0, "pnl_abs": 200.0,
        "prices_found": 2, "total_positions": 2,
    },
    {
        "date": "2026-06-06", "day": 6, "weekday": 5, "state": "weekend",
        "portfolio_value": None, "pnl_abs": None,
        "prices_found": 0, "total_positions": 0,
    },
]
_CAL_RESPONSE = {"year": 2026, "month": 6, "days": _CAL_DAYS}


def test_get_portfolio_calendar_returns_200_with_valid_params(user_client):
    with (
        patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]),
        patch("src.api.get_portfolio_calendar_data", return_value=[]),
        patch("src.api.compute_calendar_pnl", return_value=_CAL_RESPONSE),
    ):
        r = user_client.get(
            f"/api/portfolio/calendar?year=2026&month=6&portfolio_id={_CAL_PORTFOLIO_ID}",
        )
    assert r.status_code == 200
    body = r.json()
    assert body["year"] == 2026
    assert body["month"] == 6
    assert isinstance(body["days"], list)


def test_get_portfolio_calendar_returns_401_without_key(api_client):
    r = api_client.get(
        f"/api/portfolio/calendar?year=2026&month=6&portfolio_id={_CAL_PORTFOLIO_ID}",
    )
    assert r.status_code == 401


def test_get_portfolio_calendar_returns_401_with_api_key_only(api_client):
    r = api_client.get(
        f"/api/portfolio/calendar?year=2026&month=6&portfolio_id={_CAL_PORTFOLIO_ID}",
        headers={"X-API-Key": _USER_KEY},
    )
    assert r.status_code == 401


def test_get_portfolio_calendar_returns_403_for_wrong_portfolio(user_client):
    """portfolio_id belonging to a different user → 403."""
    with patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]):
        r = user_client.get(
            "/api/portfolio/calendar?year=2026&month=6&portfolio_id=other-user-portfolio",
        )
    assert r.status_code == 403


def test_get_portfolio_calendar_returns_422_when_month_out_of_range(user_client):
    with patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]):
        r = user_client.get(
            f"/api/portfolio/calendar?year=2026&month=13&portfolio_id={_CAL_PORTFOLIO_ID}",
        )
    assert r.status_code == 422


def test_get_portfolio_calendar_returns_422_when_month_zero(user_client):
    with patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]):
        r = user_client.get(
            f"/api/portfolio/calendar?year=2026&month=0&portfolio_id={_CAL_PORTFOLIO_ID}",
        )
    assert r.status_code == 422


def test_get_portfolio_calendar_returns_422_when_year_too_old(user_client):
    with patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]):
        r = user_client.get(
            f"/api/portfolio/calendar?year=2010&month=6&portfolio_id={_CAL_PORTFOLIO_ID}",
        )
    assert r.status_code == 422


def test_get_portfolio_calendar_returns_500_on_bq_error(user_client):
    from src.exceptions import BigQueryError

    with (
        patch("src.api.list_user_portfolios", return_value=[_WALLET_GLOWNY]),
        patch("src.api.get_portfolio_calendar_data", side_effect=BigQueryError("boom")),
    ):
        r = user_client.get(
            f"/api/portfolio/calendar?year=2026&month=6&portfolio_id={_CAL_PORTFOLIO_ID}",
        )
    assert r.status_code == 500


# ── Phase 6: autocomplete/etf-instruments (PUL-67) ───────────────────────────

def test_autocomplete_etf_instruments_returns_200_with_instruments(api_client):
    """GET /autocomplete/etf-instruments must return 200 with instruments list."""
    etf_data = [
        {"ticker": "ETFBW20TR", "name": "ETFBW20TR", "instrument_type": "ETF"},
        {"ticker": "ETCGLDRMAU", "name": "ETCGLDRMAU", "instrument_type": "ETC"},
    ]
    with patch("src.api.list_etf_instruments_for_autocomplete", return_value=etf_data):
        r = api_client.get("/autocomplete/etf-instruments", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 200
    body = r.json()
    assert "instruments" in body
    assert len(body["instruments"]) == 2
    assert body["instruments"][0]["ticker"] == "ETFBW20TR"


def test_autocomplete_etf_instruments_no_key_returns_401(api_client):
    r = api_client.get("/autocomplete/etf-instruments")
    assert r.status_code == 401


def test_portfolio_positions_accepts_etf_ticker(user_client):
    """POST /api/portfolio/positions must accept ETF ticker via list_distinct_portfolio_tickers."""
    with patch("src.api.list_distinct_portfolio_tickers", return_value=["CDR", "ETFBW20TR", "PKO"]), \
         patch("src.api.list_user_portfolios", return_value=[{"portfolio_id": "port-1"}]), \
         patch("src.api.upsert_user_portfolio_position"):
        r = user_client.post(
            "/api/portfolio/positions",
            json={"ticker": "ETFBW20TR", "company_name": "ETFBW20TR",
                  "shares": 5.0, "avg_buy_price": 70.0, "portfolio_id": "port-1"},
        )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"


# ── JWT cookie auth seam (PUL-71 phase 5) ─────────────────────────────────────

_JWT_SECRET = "test-jwt-secret"


def _make_session_token(
    iat_offset_seconds: int = 0, user_id: str = "fb-uid-1", role: str | None = None
) -> str:
    import time as _time

    import jwt as pyjwt

    iat = int(_time.time()) + iat_offset_seconds
    payload = {"user_id": user_id, "email": "user@example.com",
               "auth_type": "firebase", "iat": iat, "exp": iat + 7 * 24 * 3600}
    if role is not None:
        payload["role"] = role
    return pyjwt.encode(payload, _JWT_SECRET, algorithm="HS256")


@pytest.fixture
def jwt_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _JWT_SECRET)


def test_auth_role_with_cookie_only_returns_user(api_client, jwt_env):
    """A valid session cookie alone must authenticate as role=user — no API key needed."""
    api_client.cookies.set("session", _make_session_token())
    r = api_client.get("/auth/role")
    assert r.status_code == 200
    assert r.json() == {"role": "user"}


def test_auth_role_without_any_credentials_returns_401(api_client, jwt_env):
    assert api_client.get("/auth/role").status_code == 401


def test_expired_cookie_falls_through_to_api_key(api_client, jwt_env):
    """A stale browser cookie must not break API-key auth — fallthrough, not 401."""
    import time as _time

    import jwt as pyjwt

    expired = pyjwt.encode(
        {"user_id": "fb-uid-1", "email": "user@example.com", "auth_type": "firebase",
         "iat": int(_time.time()) - 7200, "exp": int(_time.time()) - 3600},
        _JWT_SECRET, algorithm="HS256",
    )
    api_client.cookies.set("session", expired)
    r = api_client.get("/auth/role", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 200
    assert r.json() == {"role": "admin"}


def test_admin_endpoint_with_cookie_returns_403(api_client, jwt_env):
    """JWT cookie grants role=user only — admin stays API-key-exclusive."""
    api_client.cookies.set("session", _make_session_token())
    assert api_client.get("/admin/x-posts").status_code == 403


def test_watchlist_with_cookie_uses_jwt_user_id(api_client, jwt_env):
    """Identity is the JWT user_id (Firebase UID) — the only source since PUL-74."""
    api_client.cookies.set("session", _make_session_token(user_id="fb-uid-42"))
    with patch("src.api.list_watchlist_tickers", return_value=["PKO"]) as lw:
        r = api_client.get("/watchlist")
    assert r.status_code == 200
    lw.assert_called_once_with("fb-uid-42")


def test_sliding_refresh_reissues_cookie_after_24h(api_client, jwt_env):
    """Token older than 24h must be re-issued via Set-Cookie; a fresh one must not."""
    api_client.cookies.set("session", _make_session_token(iat_offset_seconds=-25 * 3600))
    r = api_client.get("/auth/role")
    assert r.status_code == 200
    assert "session=" in r.headers.get("set-cookie", "")

    api_client.cookies.set("session", _make_session_token())
    r2 = api_client.get("/auth/role")
    assert r2.status_code == 200
    assert "set-cookie" not in r2.headers


# ── PUL-74 cross-user isolation ───────────────────────────────────────────────

def test_watchlist_scoped_to_calling_users_jwt(api_client, jwt_env):
    """User B's session must query with B's uid — never anyone else's."""
    api_client.cookies.set("session", _make_session_token(user_id="uid-b"))
    with patch("src.api.list_watchlist_tickers", return_value=[]) as lw:
        r = api_client.get("/watchlist")
    assert r.status_code == 200
    assert r.json() == {"tickers": []}
    lw.assert_called_once_with("uid-b")


def test_watchlist_delete_scoped_to_caller(api_client, jwt_env):
    """DELETE runs with the caller's uid — cannot reach another user's rows."""
    api_client.cookies.set("session", _make_session_token(user_id="uid-b"))
    with patch("src.api.remove_watchlist_ticker", return_value=None) as rm:
        r = api_client.delete("/watchlist/PKO")
    assert r.status_code == 204
    rm.assert_called_once_with("uid-b", "PKO")


def test_positions_user_b_cannot_read_user_a_wallet(api_client, jwt_env):
    """B asking for A's portfolio_id → 404: ownership checked against B's wallets."""
    api_client.cookies.set("session", _make_session_token(user_id="uid-b"))
    with patch("src.api.list_user_portfolios", return_value=[]) as lp:
        r = api_client.get(f"/api/portfolio/positions?portfolio_id={_WALLET_ID}")
    assert r.status_code == 404
    lp.assert_called_once_with("uid-b")


def test_positions_user_b_cannot_write_to_user_a_wallet(api_client, jwt_env):
    """B posting into A's portfolio_id → 404 and the upsert must never run."""
    api_client.cookies.set("session", _make_session_token(user_id="uid-b"))
    with (
        patch("src.api.list_user_portfolios", return_value=[]),
        patch("src.api.upsert_user_portfolio_position") as up,
    ):
        r = api_client.post(
            "/api/portfolio/positions",
            json={"portfolio_id": _WALLET_ID, "ticker": "PKO",
                  "company_name": "PKO Bank Polski SA",
                  "shares": 10.0, "avg_buy_price": 40.0},
        )
    assert r.status_code == 404
    up.assert_not_called()


def test_positions_user_b_cannot_delete_from_user_a_wallet(api_client, jwt_env):
    """B deleting from A's portfolio_id → 404 and the delete must never run."""
    api_client.cookies.set("session", _make_session_token(user_id="uid-b"))
    with (
        patch("src.api.list_user_portfolios", return_value=[]),
        patch("src.api.delete_user_portfolio_position") as dl,
    ):
        r = api_client.delete(f"/api/portfolio/positions/PKO?portfolio_id={_WALLET_ID}")
    assert r.status_code == 404
    dl.assert_not_called()


def test_calendar_user_b_cannot_read_user_a_wallet(api_client, jwt_env):
    """B requesting A's calendar → 403 and the data query must never run."""
    api_client.cookies.set("session", _make_session_token(user_id="uid-b"))
    with (
        patch("src.api.list_user_portfolios", return_value=[]),
        patch("src.api.get_portfolio_calendar_data") as gc,
    ):
        r = api_client.get(
            f"/api/portfolio/calendar?portfolio_id={_WALLET_ID}&year=2026&month=7"
        )
    assert r.status_code == 403
    gc.assert_not_called()
