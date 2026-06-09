"""Email alert delivery for pipeline failures and post notifications."""
import os
import re
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


_WINDOW_META: dict[str, dict] = {
    "Ranek":    {"color": "#0ea5e9", "emoji": "☀️"},
    "Południe": {"color": "#f59e0b", "emoji": "🌤️"},
    "Wieczór":  {"color": "#7c3aed", "emoji": "🌙"},
}


def _format_tweet_html(tweet: str) -> str:
    tweet = re.sub(r"\$([A-Z0-9]+)", r'<strong style="color:#0369a1">$\1</strong>', tweet)
    tweet = tweet.replace("▲", '<span style="color:#16a34a">▲</span>')
    tweet = tweet.replace("▼", '<span style="color:#dc2626">▼</span>')
    tweet = tweet.replace("\n", "<br>")
    return tweet


def _post_email_html(window_name: str, date_str: str, tweets: list[str]) -> str:
    meta = _WINDOW_META.get(window_name, {"color": "#374151", "emoji": "📋"})
    color = meta["color"]
    emoji = meta["emoji"]
    n = len(tweets)

    tweet_labels = (
        ["hook"]
        + [f"spółka {i}" for i in range(1, n - 1)]
        + ["closing"]
    ) if n >= 2 else ["hook"]

    blocks = ""
    for i, (tweet, label) in enumerate(zip(tweets, tweet_labels)):
        blocks += f"""
<div style="background:#ffffff;border-left:4px solid {color};padding:14px 18px;margin-top:2px;">
  <div style="font-size:11px;color:#6b7280;margin-bottom:8px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">
    𝕏 Post &nbsp;·&nbsp; Tweet {i + 1}/{n} &nbsp;·&nbsp; {label}
  </div>
  <div style="font-size:15px;line-height:1.6;color:#111827;">{_format_tweet_html(tweet)}</div>
</div>"""

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:20px;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<div style="max-width:620px;margin:0 auto;">

<div style="background:{color};color:#ffffff;padding:16px 20px;border-radius:8px 8px 0 0;">
  <span style="font-size:20px;font-weight:700;">𝕏 {emoji} {window_name}</span>
  <span style="float:right;font-size:15px;opacity:0.9;">🧵{n} &nbsp;|&nbsp; {date_str}</span>
</div>

{blocks}

<div style="background:#f9fafb;border:1px solid #e5e7eb;border-top:none;padding:12px 18px;border-radius:0 0 8px 8px;font-size:12px;color:#6b7280;text-align:center;">
  Nie jest to rekomendacja inwestycyjna. Generowane automatycznie przez puls-gpw.
</div>

</div>
</body>
</html>"""


def _send(subject: str, body: str, html: bool = False) -> None:
    host, port, user, password, owner = _smtp_creds()
    msg = MIMEText(body, "html" if html else "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = owner
    with smtplib.SMTP(host, port, timeout=10) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(user, password)
        smtp.send_message(msg)


def send_post_email(window_name: str, date_str: str, tweets: list[str]) -> None:
    """Email the approved X thread to the owner as an HTML poster."""
    meta = _WINDOW_META.get(window_name, {"emoji": "📋"})
    subject = f"{meta['emoji']} {window_name} 🧵{len(tweets)} | {date_str}"
    _send(subject, _post_email_html(window_name, date_str, tweets), html=True)


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
