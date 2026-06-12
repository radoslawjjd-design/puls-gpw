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
        "post_text": None, "posted_at": None, "analyzed_at": None,
        "supervisor_attempts": None, "parsed_content": None, "priority": None,
        "structured_analysis": None, "analysis_approved": True,
        "analysis_reject_reason": None, "event_type": "ESPI", "analysis_score": 0.8,
    }
    for i in range(20)
]


@pytest.fixture(scope="session")
def live_server_url():
    os.environ["ADMIN_API_KEY"] = _ADMIN_KEY
    os.environ["USER_API_KEY"]  = _USER_KEY

    with (
        patch("src.api.list_announcements_admin", return_value=_FAKE_ADMIN_ROWS),
        patch("src.api.list_announcements_user", return_value=[]),
    ):
        server = uvicorn.Server(
            uvicorn.Config(create_app(), host="127.0.0.1", port=18099, log_level="error")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                httpx.get("http://127.0.0.1:18099/health", timeout=1)
                break
            except Exception:
                time.sleep(0.1)

        yield "http://127.0.0.1:18099"
        server.should_exit = True
