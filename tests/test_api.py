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


client = TestClient(create_app())


def test_health_no_auth_returns_200():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_auth_role_admin_key_returns_admin():
    r = client.get("/auth/role", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 200
    assert r.json() == {"role": "admin"}


def test_auth_role_invalid_key_returns_401():
    r = client.get("/auth/role", headers={"X-API-Key": "bad-key"})
    assert r.status_code == 401


def test_announcements_admin_returns_list():
    mock_rows = [{"announcement_id": "abc", "ticker": "PKO", "title": "T",
                  "company": "C", "url": "u", "published_at": "2024-01-01T00:00:00",
                  "post_text": None, "posted_at": None, "analyzed_at": None,
                  "supervisor_attempts": None, "parsed_content": None,
                  "priority": None, "structured_analysis": None,
                  "analysis_approved": True, "analysis_reject_reason": None,
                  "event_type": "ESPI", "analysis_score": 0.9}]
    with patch("src.api.list_announcements_admin", return_value=mock_rows):
        r = client.get("/announcements", headers={"X-API-Key": _ADMIN_KEY})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1 and data[0]["ticker"] == "PKO"


def test_announcements_user_parses_structured_analysis():
    mock_rows = [{"company": "PKO", "ticker": "PKO", "event_type": "ESPI",
                  "structured_analysis": '{"summary_pl": "test"}',
                  "analysis_score": 0.8, "published_at": "2024-01-01T00:00:00"}]
    with patch("src.api.list_announcements_user", return_value=mock_rows):
        r = client.get("/announcements", headers={"X-API-Key": _USER_KEY})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data[0]["structured_analysis"], dict)
    assert data[0]["structured_analysis"]["summary_pl"] == "test"


def test_delete_user_returns_403():
    r = client.delete("/announcements/some-id",
                      headers={"X-API-Key": _USER_KEY})
    assert r.status_code == 403
