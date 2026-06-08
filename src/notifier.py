"""Email alert delivery for pipeline failures and post notifications."""
import os
import smtplib
import ssl
import traceback
from datetime import datetime, timezone
from email.mime.text import MIMEText


def _smtp_creds() -> tuple[str, int, str, str, str]:
    def _clean(key: str) -> str:
        # Secret Manager can inject BOM (﻿) and CRLF when secrets are
        # created from files with Windows line endings or UTF-8-BOM encoding.
        return os.environ[key].strip().lstrip("﻿")

    return (
        _clean("SMTP_HOST"),
        int(_clean("SMTP_PORT")),
        _clean("SMTP_USER"),
        _clean("SMTP_PASSWORD"),
        _clean("OWNER_EMAIL"),
    )


def _send(subject: str, body: str) -> None:
    host, port, user, password, owner = _smtp_creds()
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = owner
    with smtplib.SMTP(host, port, timeout=10) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(user, password)
        smtp.send_message(msg)


def send_post_email(window_name: str, date_str: str, tweets: list[str]) -> None:
    """Email the approved X thread to the owner, numbered and ready to copy-paste."""
    lines = []
    for i, tweet in enumerate(tweets):
        lines.append(f"{i + 1}/{len(tweets)}\n{tweet}")
        if i < len(tweets) - 1:
            lines.append("---")
    body = "\n".join(lines)
    _send(f"ESPI {window_name} {date_str}", body)


def send_no_post_email(window_name: str, date_str: str, reason: str) -> None:
    """Email the owner that no post was generated for this window."""
    _send(f"ESPI {window_name} {date_str} — brak posta", reason)


def send_alert(exc: BaseException) -> None:
    """Send a plain-text error alert email to the pipeline owner via SMTP/STARTTLS.

    Reads SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, OWNER_EMAIL from env.
    Does not catch exceptions — propagates to the caller, which logs and exits(1).
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    exc_type = type(exc).__name__
    body = (
        f"Pipeline failure at {timestamp}\n\n"
        f"Exception: {exc_type}: {exc}\n\n"
        f"Traceback:\n{traceback.format_exc()}"
    )
    _send(f"[puls-gpw] Pipeline ERROR: {exc_type}", body)
