import os
import threading
import time
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
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
]


_FAKE_TREEMAP_LATEST = {
    "snapshot_id": "snap-e2e-1", "wallet": "main", "snapshot_date": "2026-06-20",
    "total_value": 2000.0, "currency": "PLN",
    "day_change_abs": 100.0, "day_change_pct": 5.0,
    "positions_json": (
        '{"positions": ['
        '{"ticker": "PKO", "value": 1200.0}, '
        '{"ticker": "CDR", "value": 300.0}, '
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
        patch("src.api.get_latest_snapshot", return_value=_FAKE_TREEMAP_LATEST),
        patch("src.api.get_latest_snapshot_before", return_value=_FAKE_TREEMAP_PRIOR),
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
