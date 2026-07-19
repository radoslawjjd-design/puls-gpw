"""Email alert delivery for pipeline failures and post notifications."""
import os
import re
import smtplib
import ssl
import traceback
from datetime import datetime, timezone
from email.mime.text import MIMEText
from html import escape as _html_escape


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


_TWEET_URL = "https://x.com/i/web/status/{tweet_id}"


def _publish_banner_html(
    publish_status: str | None,
    tweet_ids: list[str] | None,
) -> str:
    """Render a status banner for the publish outcome, or "" when no result given.

    Backward-compatible: with publish_status=None the email body is unchanged.
    """
    if publish_status is None:
        return ""
    first_url = _TWEET_URL.format(tweet_id=tweet_ids[0]) if tweet_ids else None
    link = (
        f' <a href="{first_url}" style="color:inherit;text-decoration:underline;">{first_url}</a>'
        if first_url else ""
    )
    banners = {
        "published": ("#dcfce7", "#166534", f"✅ Opublikowano na X:{link}"),
        "partial": (
            "#fef3c7", "#92400e",
            f"⚠️ Publikacja częściowa ({len(tweet_ids or [])} tweetów) — sprawdź wątek:{link}",
        ),
        "failed": ("#fee2e2", "#991b1b", "❌ Publikacja na X nieudana — zobacz alert e-mail."),
        "skipped": ("#f3f4f6", "#374151", "📝 Nie opublikowano na X (szkic / auto-publish OFF)."),
    }
    bg, fg, text = banners.get(
        publish_status, ("#f3f4f6", "#374151", f"Status publikacji: {publish_status}")
    )
    return (
        f'<div style="background:{bg};color:{fg};padding:12px 18px;margin-top:2px;'
        f'font-size:13px;font-weight:600;">{text}</div>'
    )


def _post_email_html(
    window_name: str,
    date_str: str,
    tweets: list[str],
    scores: list[float | None] | None = None,
    publish_status: str | None = None,
    tweet_ids: list[str] | None = None,
) -> str:
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
        score_badge = ""
        if scores and 1 <= i <= n - 2:
            s = scores[i - 1]
            if s is not None:
                score_badge = f' &nbsp;·&nbsp; <span style="color:#059669;font-weight:700;">score {s:.0f}</span>'
        blocks += f"""
<div style="background:#ffffff;border-left:4px solid {color};padding:14px 18px;margin-top:2px;">
  <div style="font-size:11px;color:#6b7280;margin-bottom:8px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">
    𝕏 Post &nbsp;·&nbsp; Tweet {i + 1}/{n} &nbsp;·&nbsp; {label}{score_badge}
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
{_publish_banner_html(publish_status, tweet_ids)}
{blocks}

<div style="background:#f9fafb;border:1px solid #e5e7eb;border-top:none;padding:12px 18px;border-radius:0 0 8px 8px;font-size:12px;color:#6b7280;text-align:center;">
  Nie jest to rekomendacja inwestycyjna. Generowane automatycznie przez puls-gpw.
</div>

</div>
</body>
</html>"""


def _send(
    subject: str,
    body: str,
    html: bool = False,
    to: str | None = None,
    from_name: str | None = None,
) -> None:
    # `to=None` keeps the historical owner-only behavior; PUL-85 introduced
    # user-facing mail (password reset) that needs an explicit recipient.
    # `from_name` sets only the DISPLAY name — Gmail SMTP rewrites any From
    # address that doesn't match the authenticated account, so the address
    # part must stay SMTP_USER until a custom sending domain exists (see #20).
    host, port, user, password, owner = _smtp_creds()
    msg = MIMEText(body, "html" if html else "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{user}>" if from_name else user
    msg["To"] = to or owner
    with smtplib.SMTP(host, port, timeout=10) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(user, password)
        smtp.send_message(msg)


def send_post_email(
    window_name: str,
    date_str: str,
    tweets: list[str],
    scores: list[float | None] | None = None,
    publish_status: str | None = None,
    tweet_ids: list[str] | None = None,
) -> None:
    """Email the approved X thread to the owner as an HTML poster.

    When `publish_status` is given (published|skipped|failed|partial), a status
    banner is rendered above the thread. Default None keeps today's body unchanged.
    """
    meta = _WINDOW_META.get(window_name, {"emoji": "📋"})
    subject = f"{meta['emoji']} {window_name} 🧵{len(tweets)} | {date_str}"
    _send(
        subject,
        _post_email_html(window_name, date_str, tweets, scores, publish_status, tweet_ids),
        html=True,
    )


def send_no_post_email(window_name: str, date_str: str, reason: str) -> None:
    """Email the owner that no post was generated for this window."""
    _send(f"ESPI {window_name} {date_str} — brak posta", reason)


def _password_reset_html(reset_link: str, origin: str) -> str:
    # Defense-in-depth: origin derives from request headers and the link from
    # Firebase — escape both so no crafted value can break out of an HTML
    # attribute in the e-mail (AI-sec finding, PR #159). quote=True covers ".
    logo_url = _html_escape(f"{origin}/static/img/faro-mark.png", quote=True)
    reset_link = _html_escape(reset_link, quote=True)
    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:20px;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<div style="max-width:520px;margin:0 auto;">

<div style="background:#14304A;color:#ffffff;padding:18px 24px;border-radius:8px 8px 0 0;">
  <span style="display:inline-block;background:#ffffff;border-radius:8px;padding:5px 7px;vertical-align:middle;"><img src="{logo_url}" alt="Faro" height="28" style="display:block;height:28px;"></span>
  <span style="font-size:20px;font-weight:700;vertical-align:middle;margin-left:10px;">Faro</span>
</div>

<div style="background:#ffffff;padding:24px;">
  <p style="font-size:15px;line-height:1.6;color:#111827;margin:0 0 12px;">Cześć,</p>
  <p style="font-size:15px;line-height:1.6;color:#111827;margin:0 0 20px;">
    Otrzymaliśmy prośbę o zresetowanie hasła do Twojego konta w Faro.
    Kliknij poniższy przycisk, aby ustawić nowe hasło:
  </p>
  <p style="text-align:center;margin:0 0 20px;">
    <a href="{reset_link}" style="display:inline-block;background:#b8964f;color:#ffffff;padding:12px 28px;border-radius:6px;font-size:15px;font-weight:700;text-decoration:none;">Ustaw nowe hasło</a>
  </p>
  <p style="font-size:13px;line-height:1.6;color:#6b7280;margin:0 0 8px;">
    Jeśli przycisk nie działa, skopiuj ten link do przeglądarki:<br>
    <a href="{reset_link}" style="color:#8a6d23;word-break:break-all;">{reset_link}</a>
  </p>
  <p style="font-size:13px;line-height:1.6;color:#6b7280;margin:0;">
    Jeśli to nie Ty prosiłeś(-aś) o reset hasła, zignoruj tę wiadomość — Twoje
    hasło pozostaje bez zmian.
  </p>
</div>

<div style="background:#f9fafb;border:1px solid #e5e7eb;border-top:none;padding:12px 18px;border-radius:0 0 8px 8px;font-size:12px;color:#6b7280;text-align:center;">
  Faro — jasne treści komunikatów ESPI/EBI. Wiadomość wysłana automatycznie.
</div>

</div>
</body>
</html>"""


def send_password_reset_email(to_email: str, reset_link: str, origin: str) -> None:
    """PUL-85: Faro-branded password-reset e-mail (Polish) sent via own SMTP.

    Raises on SMTP failure — the caller maps it to 503. Never called for
    unknown accounts (the endpoint swallows those into a silent 204).
    """
    _send(
        "Faro — ustaw nowe hasło",
        _password_reset_html(reset_link, origin),
        html=True,
        to=to_email,
        from_name="Faro",
    )


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
