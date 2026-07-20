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
    auth_module._reset_rate_limiter._hits.clear()
    auth_module._resend_verification_rate_limiter._hits.clear()
    yield
    auth_module._register_rate_limiter._hits.clear()
    auth_module._login_rate_limiter._hits.clear()
    auth_module._reset_rate_limiter._hits.clear()
    auth_module._resend_verification_rate_limiter._hits.clear()


@pytest.fixture
def client(_env):
    return TestClient(create_app())


def _mock_firebase_user(uid="fb-uid-1", email_verified=True):
    # email_verified is set explicitly — a bare MagicMock attribute is truthy,
    # which would silently pass the login gate.
    user = MagicMock()
    user.uid = uid
    user.email_verified = email_verified
    return user


def _patch_get_user(email_verified=True):
    """Patch the login gate's Admin-SDK lookup (PUL-86)."""
    return patch(
        "src.auth.firebase_auth.get_user",
        return_value=_mock_firebase_user(email_verified=email_verified),
    )


# ── POST /api/auth/register (PUL-86: no session — verification mail instead) ──

_FAKE_VERIFY_LINK = "https://puls-gpw.firebaseapp.com/__/auth/action?mode=verifyEmail&oobCode=fake"


def test_register_happy_path_returns_pending_state_without_session(client):
    """PUL-86: register creates the account and fires the branded verification
    mail in the background — but issues NO session cookie; the continue URL
    points at the login form."""
    with patch("src.auth._get_firebase_app"), \
         patch("src.auth.firebase_auth.create_user", return_value=_mock_firebase_user()) as create_user, \
         patch("src.auth.insert_user") as insert_user, \
         patch(
             "src.auth.firebase_auth.generate_email_verification_link",
             return_value=_FAKE_VERIFY_LINK,
         ) as gen_link, \
         patch("src.auth.send_verification_email") as send_mail:
        r = client.post("/api/auth/register", json={"email": "user@example.com", "password": "haslo123"})

    assert r.status_code == 200
    assert r.json() == {"email": "user@example.com", "verification_required": True}
    assert "set-cookie" not in r.headers
    create_user.assert_called_once_with(email="user@example.com", password="haslo123")
    insert_user.assert_called_once_with("fb-uid-1", "user@example.com")
    args, kwargs = gen_link.call_args
    assert args[0] == "user@example.com"
    assert kwargs["action_code_settings"].url == "http://testserver/#/logowanie"
    send_mail.assert_called_once_with(
        "user@example.com", _FAKE_VERIFY_LINK, "http://testserver"
    )


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
         patch("src.auth.insert_user", side_effect=BigQueryError("boom")), \
         patch(
             "src.auth.firebase_auth.generate_email_verification_link",
             return_value=_FAKE_VERIFY_LINK,
         ), \
         patch("src.auth.send_verification_email") as send_mail:
        r = client.post("/api/auth/register", json={"email": "user@example.com", "password": "haslo123"})
    assert r.status_code == 200
    assert r.json()["verification_required"] is True
    send_mail.assert_called_once()


def test_register_link_generation_failure_is_silent_200_with_alert(client):
    """F1 discipline: the mail runs in the background — its failure must not
    surface to the requester (resend is the recovery path); owner gets an alert."""
    with patch("src.auth._get_firebase_app"), \
         patch("src.auth.firebase_auth.create_user", return_value=_mock_firebase_user()), \
         patch("src.auth.insert_user"), \
         patch(
             "src.auth.firebase_auth.generate_email_verification_link",
             side_effect=RuntimeError("boom"),
         ), \
         patch("src.auth.send_verification_email") as send_mail, \
         patch("src.auth.send_alert") as alert:
        r = client.post("/api/auth/register", json={"email": "user@example.com", "password": "haslo123"})

    assert r.status_code == 200
    assert r.json()["verification_required"] is True
    send_mail.assert_not_called()
    alert.assert_called_once()


def test_register_smtp_failure_is_silent_200_with_alert(client):
    with patch("src.auth._get_firebase_app"), \
         patch("src.auth.firebase_auth.create_user", return_value=_mock_firebase_user()), \
         patch("src.auth.insert_user"), \
         patch(
             "src.auth.firebase_auth.generate_email_verification_link",
             return_value=_FAKE_VERIFY_LINK,
         ), \
         patch("src.auth.send_verification_email", side_effect=OSError("smtp down")), \
         patch("src.auth.send_alert") as alert:
        r = client.post("/api/auth/register", json={"email": "user@example.com", "password": "haslo123"})

    assert r.status_code == 200
    alert.assert_called_once()


def test_register_firebase_oob_throttle_is_silent_200_without_alert(client):
    """TOO_MANY_ATTEMPTS_TRY_LATER on sendOobCode is a user-driven, self-resolving
    throttle (seen live: register + resend within seconds) — log only, NO owner
    alert; alerts must keep meaning 'mail infra is broken'."""
    from firebase_admin import auth as firebase_auth  # type: ignore[import-untyped]

    with patch("src.auth._get_firebase_app"), \
         patch("src.auth.firebase_auth.create_user", return_value=_mock_firebase_user()), \
         patch("src.auth.insert_user"), \
         patch(
             "src.auth.firebase_auth.generate_email_verification_link",
             side_effect=firebase_auth.TooManyAttemptsTryLaterError("throttled", None, None),
         ), \
         patch("src.auth.send_verification_email") as send_mail, \
         patch("src.auth.send_alert") as alert:
        r = client.post("/api/auth/register", json={"email": "user@example.com", "password": "haslo123"})

    assert r.status_code == 200
    send_mail.assert_not_called()
    alert.assert_not_called()


def test_reset_password_firebase_oob_throttle_is_silent_204_without_alert(client):
    """The reset flow shares the sendOobCode throttle exposure — same handling."""
    from firebase_admin import auth as firebase_auth  # type: ignore[import-untyped]

    with patch("src.auth._get_firebase_app"), \
         patch("src.auth.firebase_auth.get_user_by_email"), \
         patch(
             "src.auth.firebase_auth.generate_password_reset_link",
             side_effect=firebase_auth.TooManyAttemptsTryLaterError("throttled", None, None),
         ), \
         patch("src.auth.send_password_reset_email") as send_mail, \
         patch("src.auth.send_alert") as alert:
        r = client.post("/api/auth/reset-password", json={"email": "user@example.com"})

    assert r.status_code == 204
    send_mail.assert_not_called()
    alert.assert_not_called()


def test_register_crafted_host_header_is_rejected_before_account_creation(client):
    """The origin lands in HTML e-mail attributes — a crafted Host must 503
    BEFORE any Firebase account is created (same strict shape as reset)."""
    with patch("src.auth._get_firebase_app"), \
         patch("src.auth.firebase_auth.create_user") as create_user, \
         patch("src.auth.insert_user") as insert_user:
        r = client.post(
            "/api/auth/register",
            json={"email": "user@example.com", "password": "haslo123"},
            headers={"Host": 'evil"><script>alert(1)</script>'},
        )

    assert r.status_code == 503
    create_user.assert_not_called()
    insert_user.assert_not_called()


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
        with patch("src.auth._get_firebase_app"), _patch_get_user(), \
             patch("src.auth.upsert_user_login") as upsert, \
             patch("src.auth.get_user_role", return_value="user") as get_role:
            r = client.post("/api/auth/login", json={"email": "user@example.com", "password": "haslo123"})

    assert r.status_code == 200
    assert r.json() == {"user_id": "fb-uid-1", "email": "user@example.com", "role": "user"}
    assert "session=" in r.headers["set-cookie"]
    upsert.assert_called_once_with("fb-uid-1", "user@example.com")
    get_role.assert_called_once_with("fb-uid-1")


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


@pytest.mark.parametrize("failure", ["timeout", "http_500", "unknown_code", "malformed_200"])
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
        elif failure == "malformed_200":
            route.mock(return_value=HttpxResponse(200, json={"no": "localId"}))
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
        with patch("src.auth._get_firebase_app"), _patch_get_user(), \
             patch("src.auth.upsert_user_login", side_effect=BigQueryError("boom")), \
             patch("src.auth.get_user_role", return_value="user"):
            r = client.post("/api/auth/login", json={"email": "user@example.com", "password": "haslo123"})

    assert r.status_code == 200
    assert "session=" in r.headers["set-cookie"]


def test_login_admin_role_lands_in_body_cookie_and_auth_role(client):
    """PUL-83: get_user_role='admin' → role in the body, in /api/auth/me, and
    _get_role maps the cookie to admin (checked via GET /auth/role, no API key)."""
    import respx
    from httpx import Response as HttpxResponse

    with respx.mock:
        respx.post(_SIGNIN_URL).mock(
            return_value=HttpxResponse(200, json={"localId": "fb-uid-1", "email": "user@example.com"})
        )
        with patch("src.auth._get_firebase_app"), _patch_get_user(), \
             patch("src.auth.upsert_user_login"), \
             patch("src.auth.get_user_role", return_value="admin"):
            r = client.post("/api/auth/login", json={"email": "user@example.com", "password": "haslo123"})

    assert r.status_code == 200
    assert r.json()["role"] == "admin"
    assert client.get("/api/auth/me").json()["role"] == "admin"
    assert client.get("/auth/role").json() == {"role": "admin"}


def test_login_get_user_role_failure_defaults_to_user(client):
    """Availability over freshness: a BQ blip on the role read must not 5xx the login."""
    from db.bigquery import BigQueryError

    import respx
    from httpx import Response as HttpxResponse

    with respx.mock:
        respx.post(_SIGNIN_URL).mock(
            return_value=HttpxResponse(200, json={"localId": "fb-uid-1", "email": "user@example.com"})
        )
        with patch("src.auth._get_firebase_app"), _patch_get_user(), \
             patch("src.auth.upsert_user_login"), \
             patch("src.auth.get_user_role", side_effect=BigQueryError("boom")):
            r = client.post("/api/auth/login", json={"email": "user@example.com", "password": "haslo123"})

    assert r.status_code == 200
    assert r.json()["role"] == "user"


def test_login_unverified_email_returns_403_without_bq_side_effects(client):
    """PUL-86: correct password + emailVerified=false → 403 with the distinct
    message, and NO login row / role read — the gate runs before BQ."""
    import respx
    from httpx import Response as HttpxResponse

    with respx.mock:
        respx.post(_SIGNIN_URL).mock(
            return_value=HttpxResponse(200, json={"localId": "fb-uid-1", "email": "user@example.com"})
        )
        with patch("src.auth._get_firebase_app"), \
             _patch_get_user(email_verified=False), \
             patch("src.auth.upsert_user_login") as upsert, \
             patch("src.auth.get_user_role") as get_role:
            r = client.post("/api/auth/login", json={"email": "user@example.com", "password": "haslo123"})

    assert r.status_code == 403
    assert r.json()["detail"] == "Potwierdź adres e-mail, aby się zalogować"
    assert "set-cookie" not in r.headers
    upsert.assert_not_called()
    get_role.assert_not_called()


def test_login_get_user_lookup_failure_maps_to_503(client):
    """A broken emailVerified lookup must fail closed as 503 — never a 500,
    never an accidental pass through the gate."""
    import respx
    from httpx import Response as HttpxResponse

    with respx.mock:
        respx.post(_SIGNIN_URL).mock(
            return_value=HttpxResponse(200, json={"localId": "fb-uid-1", "email": "user@example.com"})
        )
        with patch("src.auth._get_firebase_app"), \
             patch("src.auth.firebase_auth.get_user", side_effect=RuntimeError("boom")), \
             patch("src.auth.upsert_user_login") as upsert:
            r = client.post("/api/auth/login", json={"email": "user@example.com", "password": "haslo123"})

    assert r.status_code == 503
    upsert.assert_not_called()


def test_garbage_role_claim_degrades_to_user(client):
    """A signed token with role='root' (not 'admin') must map to plain user."""
    from src.auth import create_session_token

    client.cookies.set(
        "session", create_session_token("uid-1", "a@b.pl", "firebase", role="root")
    )
    assert client.get("/auth/role").json() == {"role": "user"}
    assert client.get("/api/auth/me").json()["role"] == "user"  # /me normalizes like the gates


def test_legacy_token_without_role_claim_maps_to_user(client):
    """Sessions issued before PUL-83 carry no role claim — they stay valid as user."""
    import time

    import jwt as pyjwt

    now = int(time.time())
    legacy = pyjwt.encode(
        {"user_id": "uid-1", "email": "a@b.pl", "auth_type": "firebase",
         "iat": now, "exp": now + 3600, "login_at": now},
        _SECRET, algorithm="HS256",
    )
    client.cookies.set("session", legacy)
    assert client.get("/auth/role").json() == {"role": "user"}
    assert client.get("/api/auth/me").json()["role"] == "user"


# ── POST /api/auth/reset-password (PUL-85, phase 3: branded mail via own SMTP) ─

_FAKE_RESET_LINK = "https://puls-gpw.firebaseapp.com/__/auth/action?mode=resetPassword&oobCode=fake"


def test_reset_password_existing_email_returns_204_and_sends_branded_mail(client):
    """Happy path: 204 + empty body; link generated with the request origin as
    continue URL and handed to the branded mailer with the requester as recipient."""
    with patch("src.auth._get_firebase_app"), \
         patch("src.auth.firebase_auth.get_user_by_email"), \
         patch(
             "src.auth.firebase_auth.generate_password_reset_link",
             return_value=_FAKE_RESET_LINK,
         ) as gen_link, \
         patch("src.auth.send_password_reset_email") as send_mail:
        r = client.post("/api/auth/reset-password", json={"email": "user@example.com"})

    assert r.status_code == 204
    assert r.content == b""
    args, kwargs = gen_link.call_args
    assert args[0] == "user@example.com"
    assert kwargs["action_code_settings"].url == "http://testserver"
    send_mail.assert_called_once_with(
        "user@example.com", _FAKE_RESET_LINK, "http://testserver"
    )


def test_reset_password_unknown_email_returns_identical_204_without_mail(client):
    """Unknown account must collapse into the same 204 + empty body as the happy
    path, and neither link generation nor the mailer may run — no enumeration.

    The existence check is an explicit get_user_by_email: on the REAL SDK,
    generate_password_reset_link for a missing user raises a generic
    UnexpectedResponseError (NOT UserNotFoundError) — caught on prod as a
    known-204 / unknown-503 enumeration signal."""
    from firebase_admin import auth as firebase_auth  # type: ignore[import-untyped]

    with patch("src.auth._get_firebase_app"), \
         patch(
             "src.auth.firebase_auth.get_user_by_email",
             side_effect=firebase_auth.UserNotFoundError("no user"),
         ), \
         patch("src.auth.firebase_auth.generate_password_reset_link") as gen_link, \
         patch("src.auth.send_password_reset_email") as send_mail:
        r = client.post("/api/auth/reset-password", json={"email": "ghost@example.com"})

    assert r.status_code == 204
    assert r.content == b""
    gen_link.assert_not_called()
    send_mail.assert_not_called()


def test_reset_password_invalid_email_returns_422_without_calling_firebase(client):
    with patch("src.auth.firebase_auth.generate_password_reset_link") as gen_link:
        r = client.post("/api/auth/reset-password", json={"email": "not-an-email"})

    assert r.status_code == 422
    gen_link.assert_not_called()


def test_reset_password_link_generation_failure_is_silent_204_with_alert(client):
    """Impl-review F1: failures AFTER the existence check must not shape the
    response (a 503 here was a known-vs-unknown oracle) — requester gets 204,
    the owner gets an alert."""
    with patch("src.auth._get_firebase_app"), \
         patch("src.auth.firebase_auth.get_user_by_email"), \
         patch(
             "src.auth.firebase_auth.generate_password_reset_link",
             side_effect=RuntimeError("boom"),
         ), \
         patch("src.auth.send_password_reset_email") as send_mail, \
         patch("src.auth.send_alert") as alert:
        r = client.post("/api/auth/reset-password", json={"email": "user@example.com"})

    assert r.status_code == 204
    assert r.content == b""
    send_mail.assert_not_called()
    alert.assert_called_once()


def test_reset_password_firebase_unavailable_maps_to_503(client):
    """Pre-existence-check failures 503 for every input — no oracle."""
    from src.auth import AuthUnavailableError

    with patch("src.auth._get_firebase_app", side_effect=AuthUnavailableError("no config")):
        r = client.post("/api/auth/reset-password", json={"email": "user@example.com"})

    assert r.status_code == 503


def test_reset_password_smtp_failure_is_silent_204_with_alert(client):
    """Impl-review F1: an SMTP outage must not turn the endpoint into an
    account oracle — 204 for the requester, alert for the owner."""
    with patch("src.auth._get_firebase_app"), \
         patch("src.auth.firebase_auth.get_user_by_email"), \
         patch(
             "src.auth.firebase_auth.generate_password_reset_link",
             return_value=_FAKE_RESET_LINK,
         ), \
         patch("src.auth.send_password_reset_email", side_effect=OSError("smtp down")), \
         patch("src.auth.send_alert") as alert:
        r = client.post("/api/auth/reset-password", json={"email": "user@example.com"})

    assert r.status_code == 204
    assert r.content == b""
    alert.assert_called_once()


def test_reset_password_crafted_host_header_is_rejected_with_503(client):
    """AI-sec (PR #159): a Host header that breaks the strict origin shape
    (quotes, tags) must be rejected BEFORE any link/mail work — the origin is
    later embedded in HTML e-mail attributes."""
    with patch("src.auth._get_firebase_app"), \
         patch("src.auth.firebase_auth.get_user_by_email") as get_user, \
         patch("src.auth.firebase_auth.generate_password_reset_link") as gen_link, \
         patch("src.auth.send_password_reset_email") as send_mail:
        r = client.post(
            "/api/auth/reset-password",
            json={"email": "user@example.com"},
            headers={"Host": 'evil"><script>alert(1)</script>'},
        )

    assert r.status_code == 503
    get_user.assert_not_called()
    gen_link.assert_not_called()
    send_mail.assert_not_called()


def test_password_reset_html_escapes_attribute_breakout():
    """Even if a hostile origin/link reached the template, quotes must be
    neutralized inside HTML attributes."""
    from src.notifier import _password_reset_html

    html = _password_reset_html(
        'https://x.pl/act?a=1&b=2"onmouseover="alert(1)',
        'https://evil"><img src=x onerror=alert(1)>',
    )
    # Attack payloads must never appear raw — only in &quot;-escaped form.
    # (Plain '"><img' would false-positive on the template's own markup.)
    assert '"onmouseover=' not in html
    assert 'evil"><img' not in html
    assert "onerror=alert(1)" not in html.replace("&quot;&gt;&lt;img src=x onerror=alert(1)&gt;", "")
    assert "&quot;" in html


def test_verification_html_escapes_attribute_breakout():
    """Same defense-in-depth as the reset template — quotes neutralized in attributes."""
    from src.notifier import _verification_html

    html = _verification_html(
        'https://x.pl/act?a=1&b=2"onmouseover="alert(1)',
        'https://evil"><img src=x onerror=alert(1)>',
    )
    assert '"onmouseover=' not in html
    assert 'evil"><img' not in html
    assert "onerror=alert(1)" not in html.replace("&quot;&gt;&lt;img src=x onerror=alert(1)&gt;", "")
    assert "&quot;" in html


def test_reset_password_sixth_request_in_minute_returns_429_with_retry_after(client):
    """The endpoint's own limiter (5/min) throttles before Firebase is reached."""
    with patch("src.auth._get_firebase_app"), \
         patch("src.auth.firebase_auth.get_user_by_email"), \
         patch(
             "src.auth.firebase_auth.generate_password_reset_link",
             return_value=_FAKE_RESET_LINK,
         ) as gen_link, \
         patch("src.auth.send_password_reset_email"):
        for _ in range(5):
            assert client.post(
                "/api/auth/reset-password", json={"email": "user@example.com"}
            ).status_code == 204
        r = client.post("/api/auth/reset-password", json={"email": "user@example.com"})

    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert gen_link.call_count == 5  # the throttled request never reached Firebase


# ── POST /api/auth/resend-verification (PUL-86) ───────────────────────────────

def test_resend_verification_unverified_email_returns_204_and_sends_mail(client):
    with patch("src.auth._get_firebase_app"), \
         patch(
             "src.auth.firebase_auth.get_user_by_email",
             return_value=_mock_firebase_user(email_verified=False),
         ), \
         patch(
             "src.auth.firebase_auth.generate_email_verification_link",
             return_value=_FAKE_VERIFY_LINK,
         ) as gen_link, \
         patch("src.auth.send_verification_email") as send_mail:
        r = client.post("/api/auth/resend-verification", json={"email": "user@example.com"})

    assert r.status_code == 204
    assert r.content == b""
    args, kwargs = gen_link.call_args
    assert args[0] == "user@example.com"
    assert kwargs["action_code_settings"].url == "http://testserver/#/logowanie"
    send_mail.assert_called_once_with(
        "user@example.com", _FAKE_VERIFY_LINK, "http://testserver"
    )


def test_resend_verification_unknown_email_returns_identical_204_without_mail(client):
    """Unknown account → same empty 204, no link, no mail — no enumeration."""
    from firebase_admin import auth as firebase_auth  # type: ignore[import-untyped]

    with patch("src.auth._get_firebase_app"), \
         patch(
             "src.auth.firebase_auth.get_user_by_email",
             side_effect=firebase_auth.UserNotFoundError("no user"),
         ), \
         patch("src.auth.firebase_auth.generate_email_verification_link") as gen_link, \
         patch("src.auth.send_verification_email") as send_mail:
        r = client.post("/api/auth/resend-verification", json={"email": "ghost@example.com"})

    assert r.status_code == 204
    assert r.content == b""
    gen_link.assert_not_called()
    send_mail.assert_not_called()


def test_resend_verification_already_verified_returns_identical_204_without_mail(client):
    """Already-verified account → same empty 204, nothing sent — no signal,
    no redundant mail (decision from planning)."""
    with patch("src.auth._get_firebase_app"), \
         patch(
             "src.auth.firebase_auth.get_user_by_email",
             return_value=_mock_firebase_user(email_verified=True),
         ), \
         patch("src.auth.firebase_auth.generate_email_verification_link") as gen_link, \
         patch("src.auth.send_verification_email") as send_mail:
        r = client.post("/api/auth/resend-verification", json={"email": "user@example.com"})

    assert r.status_code == 204
    assert r.content == b""
    gen_link.assert_not_called()
    send_mail.assert_not_called()


def test_resend_verification_invalid_email_returns_422_without_calling_firebase(client):
    with patch("src.auth.firebase_auth.get_user_by_email") as get_user_by_email:
        r = client.post("/api/auth/resend-verification", json={"email": "not-an-email"})

    assert r.status_code == 422
    get_user_by_email.assert_not_called()


def test_resend_verification_crafted_host_header_is_rejected_with_503(client):
    with patch("src.auth._get_firebase_app"), \
         patch("src.auth.firebase_auth.get_user_by_email") as get_user_by_email, \
         patch("src.auth.send_verification_email") as send_mail:
        r = client.post(
            "/api/auth/resend-verification",
            json={"email": "user@example.com"},
            headers={"Host": 'evil"><script>alert(1)</script>'},
        )

    assert r.status_code == 503
    get_user_by_email.assert_not_called()
    send_mail.assert_not_called()


def test_resend_verification_firebase_unavailable_maps_to_503(client):
    from src.auth import AuthUnavailableError

    with patch("src.auth._get_firebase_app", side_effect=AuthUnavailableError("no config")):
        r = client.post("/api/auth/resend-verification", json={"email": "user@example.com"})

    assert r.status_code == 503


def test_resend_verification_smtp_failure_is_silent_204_with_alert(client):
    """F1 discipline: post-existence-check failures never shape the response."""
    with patch("src.auth._get_firebase_app"), \
         patch(
             "src.auth.firebase_auth.get_user_by_email",
             return_value=_mock_firebase_user(email_verified=False),
         ), \
         patch(
             "src.auth.firebase_auth.generate_email_verification_link",
             return_value=_FAKE_VERIFY_LINK,
         ), \
         patch("src.auth.send_verification_email", side_effect=OSError("smtp down")), \
         patch("src.auth.send_alert") as alert:
        r = client.post("/api/auth/resend-verification", json={"email": "user@example.com"})

    assert r.status_code == 204
    assert r.content == b""
    alert.assert_called_once()


def test_resend_verification_sixth_request_in_minute_returns_429_with_retry_after(client):
    with patch("src.auth._get_firebase_app"), \
         patch(
             "src.auth.firebase_auth.get_user_by_email",
             return_value=_mock_firebase_user(email_verified=False),
         ), \
         patch(
             "src.auth.firebase_auth.generate_email_verification_link",
             return_value=_FAKE_VERIFY_LINK,
         ) as gen_link, \
         patch("src.auth.send_verification_email"):
        for _ in range(5):
            assert client.post(
                "/api/auth/resend-verification", json={"email": "user@example.com"}
            ).status_code == 204
        r = client.post("/api/auth/resend-verification", json={"email": "user@example.com"})

    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert gen_link.call_count == 5  # the throttled request never reached Firebase


# ── POST /api/auth/logout + GET /api/auth/me ──────────────────────────────────

def _login(client) -> None:
    """Obtain a session via login — register no longer issues one (PUL-86)."""
    import respx
    from httpx import Response as HttpxResponse

    with respx.mock:
        respx.post(_SIGNIN_URL).mock(
            return_value=HttpxResponse(200, json={"localId": "fb-uid-1", "email": "user@example.com"})
        )
        with patch("src.auth._get_firebase_app"), _patch_get_user(), \
             patch("src.auth.upsert_user_login"), \
             patch("src.auth.get_user_role", return_value="user"):
            assert client.post(
                "/api/auth/login", json={"email": "user@example.com", "password": "haslo123"}
            ).status_code == 200


def test_me_without_cookie_returns_401(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_with_garbage_cookie_returns_401(client):
    client.cookies.set("session", "garbage.not.a-jwt")
    assert client.get("/api/auth/me").status_code == 401


def test_me_after_login_returns_identity_from_jwt_only(client):
    """/me must answer from the JWT alone — no BQ call (requirement from the ticket)."""
    _login(client)
    with patch("src.auth.upsert_user_login") as upsert, patch("src.auth.insert_user") as insert:
        r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json() == {"user_id": "fb-uid-1", "email": "user@example.com", "role": "user"}
    upsert.assert_not_called()
    insert.assert_not_called()


def test_logout_returns_204_and_clears_cookie(client):
    _login(client)
    r = client.post("/api/auth/logout")
    assert r.status_code == 204
    assert client.get("/api/auth/me").status_code == 401  # cookie jar honoured the deletion


def test_register_sixth_request_in_minute_returns_429_with_retry_after(client):
    with patch("src.auth._get_firebase_app"), \
         patch("src.auth.firebase_auth.create_user", return_value=_mock_firebase_user()), \
         patch("src.auth.insert_user"), \
         patch(
             "src.auth.firebase_auth.generate_email_verification_link",
             return_value=_FAKE_VERIFY_LINK,
         ), \
         patch("src.auth.send_verification_email"):
        for _ in range(5):
            assert client.post(
                "/api/auth/register", json={"email": "user@example.com", "password": "haslo123"}
            ).status_code == 200
        r = client.post("/api/auth/register", json={"email": "user@example.com", "password": "haslo123"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers
