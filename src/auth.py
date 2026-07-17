"""Auth core for PUL-71: request validation, JWT session helpers, rate limiter.

No Firebase calls here yet (phase 4); this module holds the pure logic that
backs the /api/auth/* router.
"""
import logging
import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import jwt
from email_validator import EmailNotValidError, validate_email
from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, field_validator

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


def create_session_token(user_id: str, email: str, auth_type: str) -> str:
    """Issue an HS256 session JWT valid for 7 days."""
    if not _jwt_secret():
        raise RuntimeError("JWT_SECRET is not set — cannot issue session tokens")
    now = int(time.time())
    payload = {
        "user_id": user_id,
        "email": email,
        "auth_type": auth_type,
        "iat": now,
        "exp": now + _SESSION_TTL_SECONDS,
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
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None


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
    response.delete_cookie(key=SESSION_COOKIE_NAME, httponly=True, samesite="lax")


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
