"""Auth core for PUL-71: request validation, JWT session helpers, rate limiter.

No Firebase calls here yet (phase 4); this module holds the pure logic that
backs the /api/auth/* router.
"""
import json
import logging
import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import firebase_admin  # type: ignore[import-untyped]
import httpx
import jwt
from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from firebase_admin import auth as firebase_auth  # type: ignore[import-untyped]
from firebase_admin import credentials as firebase_credentials  # type: ignore[import-untyped]
from pydantic import BaseModel, field_validator

from db.bigquery import BigQueryError, get_user_role, insert_user, upsert_user_login

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "session"
_SESSION_TTL_SECONDS = 7 * 24 * 3600


class RegisterIn(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip()
        try:
            # check_deliverability=False: syntax-only — no DNS lookups on request path
            return validate_email(v, check_deliverability=False).normalized
        except EmailNotValidError as exc:
            raise ValueError(f"Invalid email address: {exc}") from exc

    @field_validator("password")
    @classmethod
    def _valid_password(cls, v: str) -> str:
        if not 8 <= len(v) <= 128:
            raise ValueError("Password must be 8-128 characters long")
        if not any(c.isalpha() for c in v):
            raise ValueError("Password must contain at least one letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class LoginIn(RegisterIn):
    """Same validation rules as RegisterIn — junk never reaches Firebase."""


def _jwt_secret() -> str:
    # Read at call time (not import) so unit/E2E monkeypatching works — see plan phase 3
    return os.environ.get("JWT_SECRET", "")


def create_session_token(
    user_id: str,
    email: str,
    auth_type: str,
    login_at: int | None = None,
    role: str = "user",
) -> str:
    """Issue an HS256 session JWT valid for 7 days.

    `login_at` records the ORIGINAL login time and survives sliding refreshes —
    it is what the absolute session cap is measured against. Fresh logins omit
    it (login_at == iat); refresh passes the old value through.

    `role` (PUL-83) is read from BQ once at login and rides the token from
    then on — refresh MUST pass the old payload's role through, or an admin
    would silently degrade to "user" at the first 24h refresh.
    """
    if not _jwt_secret():
        raise RuntimeError("JWT_SECRET is not set — cannot issue session tokens")
    now = int(time.time())
    payload = {
        "user_id": user_id,
        "email": email,
        "auth_type": auth_type,
        "iat": now,
        "exp": now + _SESSION_TTL_SECONDS,
        "login_at": login_at if login_at is not None else now,
        "role": role,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def decode_session_token(token: str) -> dict[str, Any] | None:
    """Return the payload of a valid session token, or None (invalid/expired/tampered).

    Never raises — callers treat None as "no session".
    """
    secret = _jwt_secret()
    if not secret:
        return None
    try:
        payload = jwt.decode(
            token, secret, algorithms=["HS256"], options={"require": ["exp", "iat"]}
        )
    except jwt.InvalidTokenError:
        return None
    # Identity claims are required too — a token without them would be a
    # KeyError (500) at every consumer instead of a clean "no session".
    if not payload.get("user_id") or not payload.get("email"):
        return None
    return payload


def set_session_cookie(response: Response, token: str) -> None:
    """Attach the session cookie: HttpOnly, SameSite=Lax, Secure on Cloud Run."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        # Cloud Run sets K_SERVICE automatically — Secure on prod, not on local dev
        secure=bool(os.environ.get("K_SERVICE")),
    )


def clear_session_cookie(response: Response) -> None:
    """Emit a deletion Set-Cookie for the session cookie (logout)."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=bool(os.environ.get("K_SERVICE")),
    )


_SESSION_REFRESH_AFTER_SECONDS = 24 * 3600
_SESSION_ABSOLUTE_MAX_SECONDS = 30 * 24 * 3600


def session_payload_from_request(request: Request) -> dict[str, Any] | None:
    """Decode the session cookie from a request; None when absent/invalid/expired."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    return decode_session_token(token) if token else None


def refresh_session_if_stale(response: Response, payload: dict[str, Any]) -> None:
    """Sliding refresh: re-issue the cookie when the token is older than 24h.

    Capped at an absolute session age of 30 days from the original login
    (`login_at` claim) — without the cap a stolen cookie could be slid forever
    by replaying it daily. Past the cap the token simply isn't refreshed and
    expires naturally; the user logs in again.
    """
    now = time.time()
    if now - payload.get("iat", 0) < _SESSION_REFRESH_AFTER_SECONDS:
        return
    login_at = int(payload.get("login_at", payload.get("iat", 0)))
    if now - login_at >= _SESSION_ABSOLUTE_MAX_SECONDS:
        return
    token = create_session_token(
        payload["user_id"], payload["email"], payload.get("auth_type", "firebase"),
        login_at=login_at,
        # Pre-PUL-83 tokens carry no role claim — degrade to "user", never KeyError.
        role=payload.get("role", "user"),
    )
    set_session_cookie(response, token)


_RATE_WINDOW_SECONDS = 60.0


def client_ip(request: Request) -> str:
    """Real client IP behind Cloud Run: LAST X-Forwarded-For element.

    Google Front End appends the real client IP at the end — earlier elements
    are client-controlled (first element = trivial spoofing). Falls back to
    request.client.host for local dev without a proxy.
    """
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """In-memory sliding-window counter, per-instance by design (max 2 instances)."""

    def __init__(
        self,
        max_per_minute: int,
        time_fn: Callable[[], float] = time.time,
        sweep_threshold: int = 1000,
    ):
        self._max = max_per_minute
        self._time_fn = time_fn
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._sweep_threshold = sweep_threshold

    def check(self, ip: str) -> None:
        """Record a hit for `ip`; raise 429 with Retry-After when over the limit."""
        now = self._time_fn()
        with self._lock:
            if len(self._hits) >= self._sweep_threshold:
                # Abandoned IPs never get pruned on access — sweep so the dict is bounded
                cutoff = now - _RATE_WINDOW_SECONDS
                self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > cutoff}
            bucket = self._hits.setdefault(ip, deque())
            while bucket and bucket[0] <= now - _RATE_WINDOW_SECONDS:
                bucket.popleft()
            if len(bucket) >= self._max:
                retry_after = max(1, math.ceil(bucket[0] + _RATE_WINDOW_SECONDS - now))
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests, try again later",
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)


def rate_limit(max_per_minute: int, time_fn: Callable[[], float] = time.time):
    """Dependency factory: `Depends(rate_limit(5))` enforces max_per_minute per client IP."""
    limiter = RateLimiter(max_per_minute, time_fn)

    async def _dependency(request: Request) -> None:
        limiter.check(client_ip(request))

    return _dependency


# ── Firebase clients ──────────────────────────────────────────────────────────

class AuthUnavailableError(Exception):
    """Firebase misconfigured/unreachable — endpoints map this to 503, never 500."""


class InvalidCredentialsError(Exception):
    """Wrong email or password — one shared 401, no account enumeration."""


class FirebaseRateLimitedError(Exception):
    """Firebase-side lockout (TOO_MANY_ATTEMPTS_TRIED_LATER) — maps to 429."""


_AUTH_UNAVAILABLE_DETAIL = "Auth temporarily unavailable"

_firebase_app: firebase_admin.App | None = None
_firebase_lock = threading.Lock()


def _get_firebase_app() -> firebase_admin.App:
    """Lazy singleton — never initialized at import time (tests/E2E lack the env var)."""
    global _firebase_app
    if _firebase_app is None:
        with _firebase_lock:
            if _firebase_app is None:
                raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
                if not raw:
                    raise AuthUnavailableError("FIREBASE_SERVICE_ACCOUNT_JSON is not set")
                try:
                    cred = firebase_credentials.Certificate(json.loads(raw))
                    _firebase_app = firebase_admin.initialize_app(cred)
                except Exception as exc:
                    raise AuthUnavailableError(f"Firebase init failed: {exc}") from exc
    return _firebase_app


_IDENTITY_TOOLKIT_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"

# Identity Toolkit error codes → one shared 401 (no email/password distinction — anti-enumeration)
_INVALID_CREDENTIAL_CODES = (
    "INVALID_LOGIN_CREDENTIALS",
    "EMAIL_NOT_FOUND",
    "INVALID_PASSWORD",
    "USER_DISABLED",
)


def verify_password_rest(email: str, password: str) -> tuple[str, str]:
    """Verify credentials via Identity Toolkit REST; return (local_id, email).

    Raises InvalidCredentialsError / FirebaseRateLimitedError / AuthUnavailableError.
    """
    api_key = os.environ.get("FIREBASE_WEB_API_KEY")
    if not api_key:
        raise AuthUnavailableError("FIREBASE_WEB_API_KEY is not set")
    try:
        resp = httpx.post(
            _IDENTITY_TOOLKIT_URL,
            params={"key": api_key},
            json={"email": email, "password": password, "returnSecureToken": True},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise AuthUnavailableError(f"Identity Toolkit unreachable: {exc}") from exc

    if resp.status_code == 200:
        try:
            data = resp.json()
            return data["localId"], data.get("email", email)
        except (ValueError, KeyError) as exc:
            raise AuthUnavailableError("Identity Toolkit returned a malformed 200 body") from exc
    if resp.status_code >= 500:
        raise AuthUnavailableError(f"Identity Toolkit 5xx: {resp.status_code}")

    try:
        code = resp.json()["error"]["message"]
    except Exception as exc:
        raise AuthUnavailableError("Identity Toolkit returned an unparseable error") from exc
    # Firebase may suffix codes with context ("TOO_MANY_ATTEMPTS_TRIED_LATER : ...")
    if any(code.startswith(known) for known in _INVALID_CREDENTIAL_CODES):
        raise InvalidCredentialsError(code)
    if code.startswith("TOO_MANY_ATTEMPTS_TRIED_LATER"):
        raise FirebaseRateLimitedError(code)
    raise AuthUnavailableError(f"Identity Toolkit error: {code}")


# ── /api/auth router ──────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/auth")

_register_rate_limiter = RateLimiter(5)
_login_rate_limiter = RateLimiter(10)


async def _register_rate_dep(request: Request) -> None:
    _register_rate_limiter.check(client_ip(request))


async def _login_rate_dep(request: Request) -> None:
    _login_rate_limiter.check(client_ip(request))


def _session_response(
    response: Response, user_id: str, email: str, role: str = "user"
) -> dict[str, str]:
    token = create_session_token(user_id, email, "firebase", role=role)
    set_session_cookie(response, token)
    return {"user_id": user_id, "email": email, "role": role}


@router.post("/register")
def register(body: RegisterIn, response: Response, _rl: None = Depends(_register_rate_dep)):
    # sync (not async) on purpose: Firebase calls block up to 10s — FastAPI runs
    # def endpoints in a threadpool, so a slow Firebase never freezes the event loop
    try:
        _get_firebase_app()
        user = firebase_auth.create_user(email=body.email, password=body.password)
    except firebase_auth.EmailAlreadyExistsError:
        raise HTTPException(status_code=409, detail="Email jest już zarejestrowany")
    except AuthUnavailableError as exc:
        logger.warning("register: auth unavailable: %s", exc)
        raise HTTPException(status_code=503, detail=_AUTH_UNAVAILABLE_DETAIL)
    except Exception as exc:
        logger.error("register: unexpected Firebase error: %s", exc)
        raise HTTPException(status_code=503, detail=_AUTH_UNAVAILABLE_DETAIL)

    try:
        insert_user(user.uid, body.email)
    except BigQueryError as exc:
        # Not fatal — upsert_user_login self-heals the row on first login (Q6)
        logger.warning("register: insert_user failed for %s: %s", user.uid, exc)

    return _session_response(response, user.uid, body.email)


@router.post("/login")
def login(body: LoginIn, response: Response, _rl: None = Depends(_login_rate_dep)):
    try:
        user_id, email = verify_password_rest(body.email, body.password)
    except InvalidCredentialsError:
        raise HTTPException(status_code=401, detail="Nieprawidłowy email lub hasło")
    except FirebaseRateLimitedError:
        raise HTTPException(status_code=429, detail="Zbyt wiele prób logowania, spróbuj później")
    except AuthUnavailableError as exc:
        logger.warning("login: auth unavailable: %s", exc)
        raise HTTPException(status_code=503, detail=_AUTH_UNAVAILABLE_DETAIL)

    try:
        upsert_user_login(user_id, email)
    except BigQueryError as exc:
        # Not fatal — the row self-heals on the next successful login
        logger.warning("login: upsert_user_login failed for %s: %s", user_id, exc)

    # Availability over freshness: a BQ blip must not turn into a 5xx on
    # login — degrade to "user"; the owner regains admin at the next login.
    try:
        role = get_user_role(user_id)
    except BigQueryError as exc:
        logger.warning("login: get_user_role failed for %s: %s — defaulting to 'user'", user_id, exc)
        role = "user"

    return _session_response(response, user_id, email, role)


@router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    clear_session_cookie(response)


@router.get("/me")
def me(request: Request):
    """Identity from the JWT alone — no BQ round-trip (ticket requirement)."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    payload = decode_session_token(token) if token else None
    if payload is None:
        raise HTTPException(status_code=401, detail="Brak ważnej sesji")
    return {
        "user_id": payload["user_id"],
        "email": payload["email"],
        # Normalized like _get_role: only the exact "admin" survives — the UI
        # never sees raw/garbage claim values.
        "role": "admin" if payload.get("role") == "admin" else "user",
    }
