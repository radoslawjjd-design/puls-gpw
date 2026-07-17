"""API tests for /api/auth/* endpoints (PUL-71 phase 4) — TestClient + mocked Firebase/BQ."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api import create_app
from src.auth import verify_password_rest as _real_verify_password_rest

_SECRET = "test-jwt-secret"
_WEB_KEY = "test-web-api-key"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setenv("USER_API_KEY", "test-user-key")
    monkeypatch.setenv("JWT_SECRET", _SECRET)
    monkeypatch.setenv("FIREBASE_WEB_API_KEY", _WEB_KEY)
    # The session-scoped E2E fixture (tests/e2e/conftest.py::live_server_url)
    # patches src.auth.verify_password_rest for the remainder of the pytest
    # session once any e2e test has run. The respx-based login tests here need
    # the real function (captured at import/collection time, before fixtures
    # start) to exercise the actual Identity Toolkit error mapping.
    monkeypatch.setattr("src.auth.verify_password_rest", _real_verify_password_rest)


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Router-level limiters live for the whole process — reset between tests."""
    import src.auth as auth_module

    auth_module._register_rate_limiter._hits.clear()
    auth_module._login_rate_limiter._hits.clear()
    yield
    auth_module._register_rate_limiter._hits.clear()
    auth_module._login_rate_limiter._hits.clear()


@pytest.fixture
def client(_env):
    return TestClient(create_app())


def _mock_firebase_user(uid="fb-uid-1"):
    user = MagicMock()
    user.uid = uid
    return user


# ── POST /api/auth/register ───────────────────────────────────────────────────

def test_register_happy_path_sets_cookie_and_inserts_user(client):
    with patch("src.auth._get_firebase_app"), \
         patch("src.auth.firebase_auth.create_user", return_value=_mock_firebase_user()) as create_user, \
         patch("src.auth.insert_user") as insert_user:
        r = client.post("/api/auth/register", json={"email": "user@example.com", "password": "haslo123"})

    assert r.status_code == 200
    assert r.json() == {"user_id": "fb-uid-1", "email": "user@example.com"}
    cookie_header = r.headers["set-cookie"]
    assert cookie_header.startswith("session=")
    assert "HttpOnly" in cookie_header
    create_user.assert_called_once_with(email="user@example.com", password="haslo123")
    insert_user.assert_called_once_with("fb-uid-1", "user@example.com")


@pytest.mark.parametrize(
    "body",
    [
        {"email": "user@example.com", "password": "krotki1"},   # 7 chars
        {"email": "user@example.com", "password": "bezcyfry"},  # no digit
        {"email": "not-an-email", "password": "haslo123"},      # bad email
    ],
)
def test_register_invalid_input_returns_422_without_touching_firebase(client, body):
    with patch("src.auth.firebase_auth.create_user") as create_user:
        r = client.post("/api/auth/register", json=body)
    assert r.status_code == 422
    create_user.assert_not_called()


def test_register_existing_email_returns_409(client):
    from firebase_admin import auth as firebase_auth  # type: ignore[import-untyped]

    with patch("src.auth._get_firebase_app"), \
         patch(
             "src.auth.firebase_auth.create_user",
             side_effect=firebase_auth.EmailAlreadyExistsError("exists", None, None),
         ):
        r = client.post("/api/auth/register", json={"email": "user@example.com", "password": "haslo123"})
    assert r.status_code == 409
    assert r.json()["detail"] == "Email jest już zarejestrowany"


def test_register_bq_failure_is_logged_not_blocking(client):
    """INSERT failure must not fail registration — login self-heals the row later (Q6)."""
    from db.bigquery import BigQueryError

    with patch("src.auth._get_firebase_app"), \
         patch("src.auth.firebase_auth.create_user", return_value=_mock_firebase_user()), \
         patch("src.auth.insert_user", side_effect=BigQueryError("boom")):
        r = client.post("/api/auth/register", json={"email": "user@example.com", "password": "haslo123"})
    assert r.status_code == 200
    assert "session=" in r.headers.get("set-cookie", "")


def test_register_firebase_unavailable_returns_503(client):
    """Missing/broken Firebase config must map to 503, never a raw 500."""
    from src.auth import AuthUnavailableError

    with patch("src.auth._get_firebase_app", side_effect=AuthUnavailableError("no config")):
        r = client.post("/api/auth/register", json={"email": "user@example.com", "password": "haslo123"})
    assert r.status_code == 503


# ── POST /api/auth/login ──────────────────────────────────────────────────────

_SIGNIN_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"


def test_login_happy_path_sets_cookie_and_upserts_login(client):
    import respx
    from httpx import Response as HttpxResponse

    with respx.mock:
        respx.post(_SIGNIN_URL).mock(
            return_value=HttpxResponse(200, json={"localId": "fb-uid-1", "email": "user@example.com"})
        )
        with patch("src.auth.upsert_user_login") as upsert:
            r = client.post("/api/auth/login", json={"email": "user@example.com", "password": "haslo123"})

    assert r.status_code == 200
    assert r.json() == {"user_id": "fb-uid-1", "email": "user@example.com"}
    assert "session=" in r.headers["set-cookie"]
    upsert.assert_called_once_with("fb-uid-1", "user@example.com")


@pytest.mark.parametrize(
    "code",
    ["INVALID_LOGIN_CREDENTIALS", "EMAIL_NOT_FOUND", "INVALID_PASSWORD", "USER_DISABLED"],
)
def test_login_wrong_credentials_map_to_shared_401(client, code):
    """All four Identity Toolkit codes collapse into one 401 message (anti-enumeration)."""
    import respx
    from httpx import Response as HttpxResponse

    with respx.mock:
        respx.post(_SIGNIN_URL).mock(
            return_value=HttpxResponse(400, json={"error": {"message": code}})
        )
        r = client.post("/api/auth/login", json={"email": "user@example.com", "password": "haslo123"})

    assert r.status_code == 401
    assert r.json()["detail"] == "Nieprawidłowy email lub hasło"


def test_login_firebase_lockout_maps_to_429(client):
    """TOO_MANY_ATTEMPTS_TRIED_LATER (with Firebase's suffix) → 429, no Retry-After."""
    import respx
    from httpx import Response as HttpxResponse

    with respx.mock:
        respx.post(_SIGNIN_URL).mock(
            return_value=HttpxResponse(
                400,
                json={"error": {"message": "TOO_MANY_ATTEMPTS_TRIED_LATER : Try again later."}},
            )
        )
        r = client.post("/api/auth/login", json={"email": "user@example.com", "password": "haslo123"})

    assert r.status_code == 429


@pytest.mark.parametrize("failure", ["timeout", "http_500", "unknown_code"])
def test_login_service_failures_map_to_503(client, failure):
    import httpx as httpx_module
    import respx
    from httpx import Response as HttpxResponse

    with respx.mock:
        route = respx.post(_SIGNIN_URL)
        if failure == "timeout":
            route.mock(side_effect=httpx_module.ConnectTimeout("timeout"))
        elif failure == "http_500":
            route.mock(return_value=HttpxResponse(500, json={}))
        else:
            route.mock(return_value=HttpxResponse(400, json={"error": {"message": "WEIRD_NEW_CODE"}}))
        r = client.post("/api/auth/login", json={"email": "user@example.com", "password": "haslo123"})

    assert r.status_code == 503


def test_login_bq_upsert_failure_is_logged_not_blocking(client):
    from db.bigquery import BigQueryError

    import respx
    from httpx import Response as HttpxResponse

    with respx.mock:
        respx.post(_SIGNIN_URL).mock(
            return_value=HttpxResponse(200, json={"localId": "fb-uid-1", "email": "user@example.com"})
        )
        with patch("src.auth.upsert_user_login", side_effect=BigQueryError("boom")):
            r = client.post("/api/auth/login", json={"email": "user@example.com", "password": "haslo123"})

    assert r.status_code == 200
    assert "session=" in r.headers["set-cookie"]


# ── POST /api/auth/logout + GET /api/auth/me ──────────────────────────────────

def _register(client) -> None:
    with patch("src.auth._get_firebase_app"), \
         patch("src.auth.firebase_auth.create_user", return_value=_mock_firebase_user()), \
         patch("src.auth.insert_user"):
        assert client.post(
            "/api/auth/register", json={"email": "user@example.com", "password": "haslo123"}
        ).status_code == 200


def test_me_without_cookie_returns_401(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_with_garbage_cookie_returns_401(client):
    client.cookies.set("session", "garbage.not.a-jwt")
    assert client.get("/api/auth/me").status_code == 401


def test_me_after_register_returns_identity_from_jwt_only(client):
    """/me must answer from the JWT alone — no BQ call (requirement from the ticket)."""
    _register(client)
    with patch("src.auth.upsert_user_login") as upsert, patch("src.auth.insert_user") as insert:
        r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json() == {"user_id": "fb-uid-1", "email": "user@example.com"}
    upsert.assert_not_called()
    insert.assert_not_called()


def test_logout_returns_204_and_clears_cookie(client):
    _register(client)
    r = client.post("/api/auth/logout")
    assert r.status_code == 204
    assert client.get("/api/auth/me").status_code == 401  # cookie jar honoured the deletion


def test_register_sixth_request_in_minute_returns_429_with_retry_after(client):
    with patch("src.auth._get_firebase_app"), \
         patch("src.auth.firebase_auth.create_user", return_value=_mock_firebase_user()), \
         patch("src.auth.insert_user"):
        for _ in range(5):
            assert client.post(
                "/api/auth/register", json={"email": "user@example.com", "password": "haslo123"}
            ).status_code == 200
        r = client.post("/api/auth/register", json={"email": "user@example.com", "password": "haslo123"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers
