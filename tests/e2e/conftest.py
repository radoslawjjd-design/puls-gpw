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
