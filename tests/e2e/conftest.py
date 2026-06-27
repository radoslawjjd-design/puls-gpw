import os
import threading
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import uvicorn

from src.api import create_app


_ADMIN_KEY = "e2e-admin-key"
_USER_KEY  = "e2e-user-key"

_FAKE_ADMIN_ROWS = [
    {
        "announcement_id": f"id{i}", "url": f"http://example.com/{i}",
        "published_at": datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        "title": f"Ann {i}", "company": "PKO SA", "ticker": "PKO",
        "post_text": None, "posted_at": None, "x_post_id": None, "analyzed_at": None,
        "supervisor_attempts": None, "parsed_content": None, "priority": None,
        "structured_analysis": None, "analysis_approved": True,
        "analysis_reject_reason": None, "event_type": "ESPI", "analysis_score": 0.8,
    }
    for i in range(20)
]

_FAKE_X_POSTS_ROWS = [
    {
        "x_post_id": "post-pub-1", "window": "ranek",
        "post_text": "Pierwszy tweet PASSUS\n\nDrugi tweet wątku",
        "tweet_ids": "1111111111,2222222222",
        "posted_at": datetime(2026, 6, 18, 8, 0, 0, tzinfo=timezone.utc),
        "supervisor_attempts": 1, "x_publish_status": "published",
    },
    {
        "x_post_id": "post-partial-1", "window": "poludnie",
        "post_text": "Tweet A częściowy\n\nTweet B bez id\n\nTweet C bez id",
        "tweet_ids": "3333333333",
        "posted_at": datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc),
        "supervisor_attempts": 2, "x_publish_status": "partial",
    },
    {
        "x_post_id": None, "window": "wieczor",
        "post_text": None, "tweet_ids": None,
        "posted_at": datetime(2026, 6, 18, 18, 0, 0, tzinfo=timezone.utc),
        "supervisor_attempts": 3, "x_publish_status": "failed",
    },
    # Padding rows so the unfiltered AND the `x_publish_status=skipped` filtered
    # count both reach the default page_size (20) — without these, the
    # "Następna" button is permanently disabled (3 rows < 20) and url-routing
    # pagination tests have nothing to click into a real page 2.
    *[
        {
            "x_post_id": f"pad-{i}", "window": "test",
            "post_text": None, "tweet_ids": None,
            "posted_at": datetime(2026, 6, 10, 8, 0, 0, tzinfo=timezone.utc),
            "supervisor_attempts": 1, "x_publish_status": "skipped",
        }
        for i in range(20)
    ],
]


_FAKE_TREEMAP_LATEST = {
    "snapshot_id": "snap-e2e-1", "wallet": "main", "snapshot_date": "2026-06-20",
    "total_value": 2000.0, "currency": "PLN",
    "day_change_abs": 100.0, "day_change_pct": 5.0,
    "positions_json": (
        '{"positions": ['
        '{"ticker": "PKO", "value": 1200.0, "pct": 20.0}, '
        '{"ticker": "CDR", "value": 300.0, "pct": 50.0}, '
        '{"ticker": "NEW", "value": 500.0}'
        '], "media_attached": false}'
    ),
}

_FAKE_TREEMAP_PRIOR = {
    "snapshot_id": "snap-e2e-0", "wallet": "main", "snapshot_date": "2026-06-19",
    "total_value": 1900.0, "currency": "PLN",
    "day_change_abs": 0.0, "day_change_pct": 0.0,
    "positions_json": (
        '{"positions": ['
        '{"ticker": "PKO", "value": 1000.0}, '
        '{"ticker": "CDR", "value": 400.0}'
        '], "media_attached": false}'
    ),
}


_FAKE_TREEMAP_IKZE_LATEST = {
    "snapshot_id": "snap-e2e-ikze-1", "wallet": "ikze", "snapshot_date": "2026-06-20",
    "total_value": 1000.0, "currency": "PLN",
    "day_change_abs": 50.0, "day_change_pct": 5.0,
    "positions_json": (
        '{"positions": ['
        '{"ticker": "ALE", "value": 700.0, "pct": 40.0}, '
        '{"ticker": "KGH", "value": 300.0, "pct": 20.0}'
        '], "media_attached": false}'
    ),
}

_FAKE_TREEMAP_IKZE_PRIOR = {
    "snapshot_id": "snap-e2e-ikze-0", "wallet": "ikze", "snapshot_date": "2026-06-19",
    "total_value": 900.0, "currency": "PLN",
    "day_change_abs": 0.0, "day_change_pct": 0.0,
    "positions_json": (
        '{"positions": ['
        '{"ticker": "ALE", "value": 600.0}, '
        '{"ticker": "KGH", "value": 350.0}'
        '], "media_attached": false}'
    ),
}


def _fake_get_latest_snapshot_for_wallet(wallet):
    """Patches get_latest_snapshot_for_wallet (PUL-50) — keyed by wallet so both
    `main` and `ikze` render with their own real e2e fixture data."""
    if wallet == "main":
        return _FAKE_TREEMAP_LATEST
    if wallet == "ikze":
        return _FAKE_TREEMAP_IKZE_LATEST
    return None


def _fake_get_latest_snapshot_before(wallet, before_date):
    """Patches get_latest_snapshot_before (PUL-50) — keyed by wallet so each
    wallet's daily delta is computed against its own prior snapshot."""
    if wallet == "main":
        return _FAKE_TREEMAP_PRIOR
    if wallet == "ikze":
        return _FAKE_TREEMAP_IKZE_PRIOR
    return None


# In-memory watchlist store keyed by client_id, mirroring the real BQ
# semantics (idempotent add, no-op-safe remove, most-recently-added first).
# Session-scoped like live_server_url, but each test gets a fresh browser
# context (and so a fresh `watchlist_client_id`), so tests never collide.
_watchlist_store: dict[str, list[str]] = {}

_FAKE_WATCHLIST_ANNOUNCEMENT = {
    "company": "PKO SA", "ticker": "PKO", "event_type": "ESPI",
    "structured_analysis": None,
    "published_at": datetime(2026, 6, 20, 9, 0, 0, tzinfo=timezone.utc),
}


def _fake_add_watchlist_ticker(client_id, ticker):
    tickers = _watchlist_store.setdefault(client_id, [])
    if ticker not in tickers:
        tickers.insert(0, ticker)


def _fake_remove_watchlist_ticker(client_id, ticker):
    tickers = _watchlist_store.get(client_id, [])
    if ticker in tickers:
        tickers.remove(ticker)


def _fake_list_watchlist_tickers(client_id):
    return list(_watchlist_store.get(client_id, []))


def _fake_list_announcements_for_watchlist(client_id, page=1, page_size=20, from_dt=None, to_dt=None):
    if "PKO" in _watchlist_store.get(client_id, []):
        return [_FAKE_WATCHLIST_ANNOUNCEMENT]
    return []


def _fake_list_x_posts_admin(
    page=1, page_size=20, window=None, x_publish_status=None,
    post_text=None, from_dt=None, to_dt=None,
):
    """Mirrors list_x_posts_admin's filter semantics so the live E2E server
    exercises real filter/pagination narrowing instead of a fixed payload."""
    rows = _FAKE_X_POSTS_ROWS
    if window:
        rows = [r for r in rows if r["window"] == window]
    if x_publish_status:
        rows = [r for r in rows if r["x_publish_status"] == x_publish_status]
    if post_text:
        rows = [r for r in rows if r["post_text"] and post_text.lower() in r["post_text"].lower()]
    if from_dt:
        rows = [r for r in rows if r["posted_at"] >= from_dt]
    if to_dt:
        rows = [r for r in rows if r["posted_at"] <= to_dt]
    rows = sorted(rows, key=lambda r: r["posted_at"], reverse=True)
    start = (page - 1) * page_size
    return rows[start:start + page_size]


@pytest.fixture(autouse=True)
def _accept_gdpr(page, request):
    """Pre-accept GDPR consent so the banner never blocks existing tests.
    Skip for tests marked @pytest.mark.gdpr — those need the real banner."""
    if "gdpr" not in request.node.keywords:
        page.add_init_script("localStorage.setItem('gdpr_consent_v1', 'accepted')")


@pytest.fixture(scope="session")
def live_server_url():
    os.environ["ADMIN_API_KEY"] = _ADMIN_KEY
    os.environ["USER_API_KEY"]  = _USER_KEY

    with (
        patch("src.api.list_announcements_admin", return_value=_FAKE_ADMIN_ROWS),
        patch("src.api.list_announcements_user", return_value=[]),
        patch("src.api.list_distinct_tickers",   return_value=["PKO", "CDR", "XTB"]),
        patch("src.api.list_distinct_companies",  return_value=["PKO SA", "CD Projekt SA"]),
        patch("src.api.list_x_posts_admin", side_effect=_fake_list_x_posts_admin),
        patch("src.api.get_latest_snapshot_for_wallet", side_effect=_fake_get_latest_snapshot_for_wallet),
        patch("src.api.get_latest_snapshot_before", side_effect=_fake_get_latest_snapshot_before),
        patch("src.api.get_latest_company_stats_fetched_at", return_value="2026-06-27T09:01:05+00:00"),
        patch("src.api.create_watchlist_table_if_not_exists"),
        patch("src.api.ensure_watchlist_schema_current"),
        patch("src.api.create_companies_table_if_not_exists"),
        patch("src.api.ensure_companies_schema_current"),
        patch("src.api.add_watchlist_ticker", side_effect=_fake_add_watchlist_ticker),
        patch("src.api.remove_watchlist_ticker", side_effect=_fake_remove_watchlist_ticker),
        patch("src.api.list_watchlist_tickers", side_effect=_fake_list_watchlist_tickers),
        patch("src.api.list_announcements_for_watchlist", side_effect=_fake_list_announcements_for_watchlist),
    ):
        server = uvicorn.Server(
            uvicorn.Config(create_app(), host="127.0.0.1", port=0, log_level="error")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        deadline = time.time() + 5
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"

        yield base_url
        server.should_exit = True
        thread.join(timeout=3)
