"""Unit tests for src/auth.py — validators, JWT session helpers, rate limiter (PUL-71)."""
import pytest
from pydantic import ValidationError


# ── RegisterIn / LoginIn validation ───────────────────────────────────────────

def test_register_in_accepts_valid_email_and_password():
    """A well-formed email and an 8+ char password with letter+digit must pass."""
    from src.auth import RegisterIn

    model = RegisterIn(email="user@example.com", password="haslo123")
    assert model.email == "user@example.com"
    assert model.password == "haslo123"


def test_register_in_strips_email_whitespace():
    """Surrounding whitespace must be stripped before validation, not rejected."""
    from src.auth import RegisterIn

    model = RegisterIn(email="  user@example.com  ", password="haslo123")
    assert model.email == "user@example.com"


def test_register_in_rejects_invalid_email():
    from src.auth import RegisterIn

    with pytest.raises(ValidationError):
        RegisterIn(email="not-an-email", password="haslo123")


@pytest.mark.parametrize(
    "password",
    [
        "krotk17",        # 7 chars — below minimum 8
        "a1" + "x" * 127, # 129 chars — above maximum 128
        "bezcyfry",       # no digit
        "12345678",       # no letter
    ],
)
def test_register_in_rejects_bad_passwords(password):
    """Password must be 8-128 chars with at least one letter and one digit."""
    from src.auth import RegisterIn

    with pytest.raises(ValidationError):
        RegisterIn(email="user@example.com", password=password)


def test_register_in_accepts_boundary_lengths():
    """Exactly 8 and exactly 128 chars are valid (boundaries inclusive)."""
    from src.auth import RegisterIn

    RegisterIn(email="user@example.com", password="abcdefg1")            # 8
    RegisterIn(email="user@example.com", password="a1" + "x" * 126)      # 128


def test_login_in_applies_same_rules():
    """LoginIn shares the validators — junk never reaches Firebase."""
    from src.auth import LoginIn

    model = LoginIn(email=" user@example.com ", password="haslo123")
    assert model.email == "user@example.com"
    with pytest.raises(ValidationError):
        LoginIn(email="user@example.com", password="short")


# ── JWT session tokens + cookie helpers ───────────────────────────────────────

_SECRET = "test-jwt-secret"


@pytest.fixture
def jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _SECRET)


def test_session_token_roundtrip(jwt_secret):
    """Token created by create_session_token must decode back to its payload; exp = iat + 7 days."""
    from src.auth import create_session_token, decode_session_token

    token = create_session_token("uid-1", "user@example.com", "firebase")
    payload = decode_session_token(token)
    assert payload is not None
    assert payload["user_id"] == "uid-1"
    assert payload["email"] == "user@example.com"
    assert payload["auth_type"] == "firebase"
    assert payload["exp"] - payload["iat"] == 7 * 24 * 3600


def test_decode_returns_none_for_tampered_token(jwt_secret):
    """A token signed with a different secret must decode to None, never raise."""
    import jwt as pyjwt

    from src.auth import decode_session_token

    forged = pyjwt.encode({"user_id": "uid-1"}, "wrong-secret", algorithm="HS256")
    assert decode_session_token(forged) is None
    assert decode_session_token("garbage.not.a-jwt") is None


def test_decode_returns_none_for_expired_token(jwt_secret):
    """A token past its exp must decode to None."""
    import time

    import jwt as pyjwt

    from src.auth import decode_session_token

    expired = pyjwt.encode(
        {"user_id": "uid-1", "iat": int(time.time()) - 7200, "exp": int(time.time()) - 3600},
        _SECRET,
        algorithm="HS256",
    )
    assert decode_session_token(expired) is None


def test_jwt_secret_read_at_call_time(monkeypatch):
    """JWT_SECRET must be read inside the call, not at import — monkeypatch after import works."""
    from src.auth import create_session_token, decode_session_token

    monkeypatch.setenv("JWT_SECRET", "first-secret")
    token = create_session_token("uid-1", "a@b.pl", "firebase")
    monkeypatch.setenv("JWT_SECRET", "second-secret")
    assert decode_session_token(token) is None  # signature no longer matches


def test_set_session_cookie_flags_local(jwt_secret, monkeypatch):
    """Cookie 'session' must be HttpOnly + SameSite=lax; no Secure without K_SERVICE."""
    from fastapi import Response

    from src.auth import set_session_cookie

    monkeypatch.delenv("K_SERVICE", raising=False)
    response = Response()
    set_session_cookie(response, "tok-value")
    header = response.headers["set-cookie"]
    assert header.startswith("session=")
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Secure" not in header


def test_set_session_cookie_secure_on_cloud_run(jwt_secret, monkeypatch):
    """With K_SERVICE set (Cloud Run), the cookie must carry the Secure flag."""
    from fastapi import Response

    from src.auth import set_session_cookie

    monkeypatch.setenv("K_SERVICE", "puls-gpw-api")
    response = Response()
    set_session_cookie(response, "tok-value")
    assert "Secure" in response.headers["set-cookie"]


def test_clear_session_cookie_expires_it():
    """clear_session_cookie must emit a deletion Set-Cookie for 'session'."""
    from fastapi import Response

    from src.auth import clear_session_cookie

    response = Response()
    clear_session_cookie(response)
    header = response.headers["set-cookie"]
    assert header.startswith('session="";') or header.startswith("session=;")
    assert 'Max-Age=0' in header or "01 Jan 1970" in header


# ── rate limiter ──────────────────────────────────────────────────────────────

def _make_request(headers: dict[str, str] | None = None, client_host: str = "10.0.0.1"):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (client_host, 1234),
        "query_string": b"",
    }
    return Request(scope)


def test_client_ip_uses_last_xff_element():
    """Google Front End appends the real client IP LAST — earlier elements are spoofable."""
    from src.auth import client_ip

    request = _make_request({"X-Forwarded-For": "6.6.6.6, 203.0.113.7"})
    assert client_ip(request) == "203.0.113.7"


def test_client_ip_falls_back_to_client_host():
    """Without X-Forwarded-For (local dev), request.client.host is used."""
    from src.auth import client_ip

    assert client_ip(_make_request(client_host="127.0.0.1")) == "127.0.0.1"


def test_rate_limiter_returns_429_above_limit():
    """The (max+1)-th request in the window must raise 429 with a Retry-After header."""
    from fastapi import HTTPException

    from src.auth import RateLimiter

    fake_now = [1000.0]
    limiter = RateLimiter(max_per_minute=3, time_fn=lambda: fake_now[0])
    for _ in range(3):
        limiter.check("1.2.3.4")
    with pytest.raises(HTTPException) as exc_info:
        limiter.check("1.2.3.4")
    assert exc_info.value.status_code == 429
    retry_after = int(exc_info.value.headers["Retry-After"])
    assert 0 < retry_after <= 60


def test_rate_limiter_window_slides():
    """Slots older than 60s free up — a request after the window must pass again."""
    from src.auth import RateLimiter

    fake_now = [1000.0]
    limiter = RateLimiter(max_per_minute=2, time_fn=lambda: fake_now[0])
    limiter.check("1.2.3.4")
    limiter.check("1.2.3.4")
    fake_now[0] = 1061.0  # both slots now outside the 60s window
    limiter.check("1.2.3.4")  # must not raise


def test_rate_limiter_buckets_are_per_ip():
    """One client hitting the limit must not affect another IP's bucket."""
    from fastapi import HTTPException

    from src.auth import RateLimiter

    limiter = RateLimiter(max_per_minute=1, time_fn=lambda: 1000.0)
    limiter.check("1.1.1.1")
    with pytest.raises(HTTPException):
        limiter.check("1.1.1.1")
    limiter.check("2.2.2.2")  # different bucket — must not raise


def test_spoofed_xff_first_element_does_not_bypass_limit():
    """Rotating the client-controlled FIRST XFF element must not escape the bucket —
    the key comes from the LAST element (GFE-appended real IP)."""
    from fastapi import HTTPException

    from src.auth import RateLimiter, client_ip

    limiter = RateLimiter(max_per_minute=2, time_fn=lambda: 1000.0)
    for i in range(2):
        request = _make_request({"X-Forwarded-For": f"spoof-{i}, 203.0.113.7"})
        limiter.check(client_ip(request))
    request = _make_request({"X-Forwarded-For": "spoof-fresh, 203.0.113.7"})
    with pytest.raises(HTTPException) as exc_info:
        limiter.check(client_ip(request))
    assert exc_info.value.status_code == 429


def _drive(coro):
    """Run a coroutine that never awaits (avoids asyncio.run — other tests leave loops behind)."""
    try:
        coro.send(None)
    except StopIteration:
        return
    raise AssertionError("dependency unexpectedly awaited something")


def test_rate_limit_factory_returns_fastapi_dependency():
    """rate_limit(n) must produce an async dependency that enforces the limit per request."""
    from fastapi import HTTPException

    from src.auth import rate_limit

    dep = rate_limit(1, time_fn=lambda: 1000.0)
    request = _make_request({"X-Forwarded-For": "6.6.6.6, 203.0.113.7"})
    _drive(dep(request))
    with pytest.raises(HTTPException) as exc_info:
        _drive(dep(request))
    assert exc_info.value.status_code == 429
