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
def _clear_ac_cache():
    import src.api as m
    m._AC_CACHE.clear()
    yield
    m._AC_CACHE.clear()


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
    ):
        r = api_client.get("/admin/portfolio/treemap", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 200
    body = r.json()
    assert list(body.keys()) == ["main", "ikze"]
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
    assert r.json() == {"main": [], "ikze": []}


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
