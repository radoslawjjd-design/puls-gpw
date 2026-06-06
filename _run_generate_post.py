"""One-off script: fetch top-4 from BQ and run generate_post. Manual verification of 2.4."""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

import os
import re
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from db.bigquery import fetch_top_n_for_window
from src.post_generator import generate_post
from src.post_supervisor import validate_post

# ── Window config ─────────────────────────────────────────────────────────────

_WINDOW_COLORS = {
    "ranek":    ("#0ea5e9", "🌅", "Ranek"),
    "poludnie": ("#f59e0b", "🌤️",  "Południe"),
    "wieczor":  ("#7c3aed", "🌆", "Wieczór"),
    "test":     ("#374151", "🧪", "Test"),
}

# ── BQ fetch ──────────────────────────────────────────────────────────────────

end = datetime.now(tz=timezone.utc)
start = end - timedelta(days=7)
date_str = datetime.now().strftime("%d.%m.%Y")

print(f"Window: {start.isoformat()} to {end.isoformat()}")

announcements = fetch_top_n_for_window(start, end, n=4)
print(f"Fetched {len(announcements)} announcement(s):")
for a in announcements:
    print(f"  [{a['ticker']}] {a['company']} | score={a['analysis_score']} | {a['event_type']}")

if len(announcements) < 2:
    print("Less than 2 approved announcements — cannot generate post.")
    sys.exit(0)

# ── Generate + validate ───────────────────────────────────────────────────────

print("\nCalling generate_post …")
post = generate_post(announcements)

if post is None:
    print("generate_post returned None.")
    sys.exit(1)

# Deduplicate tickers the same way generate_post does, for expected count
unique_tickers = list(dict.fromkeys(a["ticker"] for a in announcements if a.get("ticker")))
tickers = unique_tickers
expected_tweets = len(unique_tickers) + 2
validation = validate_post(post, tickers, expected_tweets=expected_tweets)

print(f"Supervisor: approved={validation.approved}")
for issue in validation.issues:
    print(f"  - {issue}")

# ── Console preview ───────────────────────────────────────────────────────────

print()
for i, tweet in enumerate(post.tweets, 1):
    print(f"--- Tweet {i}/{len(post.tweets)} ({len(tweet)} chars) ---")
    print(tweet)
    print()

# ── HTML helpers ──────────────────────────────────────────────────────────────

def _format_tweet_html(text: str) -> str:
    """$TICKER → bold blue, financial numbers → bold, ▲/▼/→ → coloured, newlines → <br>."""
    text = re.sub(
        r"\$([A-Z]{2,6})",
        r'<strong style="color:#0369a1">$\1</strong>',
        text,
    )
    text = re.sub(
        r"(\d[\d\s]*[,.]?\d*)\s*(mln|mld|tys\.?|PLN|%)",
        lambda m: f"<strong>{m.group(1)} {m.group(2)}</strong>",
        text,
    )
    text = re.sub(r"▲", '<span style="color:#16a34a">▲</span>', text)
    text = re.sub(r"▼", '<span style="color:#dc2626">▼</span>', text)
    text = re.sub(r"→", '<span style="color:#7c3aed;font-weight:600">→</span>', text)
    text = text.replace("\n", "<br>")
    return text


def _char_badge(n: int) -> str:
    if n <= 230:
        colour = "#16a34a"
    elif n <= 260:
        colour = "#d97706"
    else:
        colour = "#dc2626"
    return (
        f'<span style="font-size:11px;color:{colour};font-weight:600">'
        f"{n}/280 zn.</span>"
    )


def _supervisor_badge(approved: bool) -> str:
    if approved:
        return '<span style="background:#dcfce7;color:#166534;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600">✅ Supervisor: approved</span>'
    return '<span style="background:#fee2e2;color:#991b1b;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600">❌ Supervisor: rejected</span>'


def build_html(
    tweets: list[str],
    announcements: list[dict],
    validation,
    window_key: str = "test",
    date_str: str = "",
) -> str:
    color, emoji, window_name = _WINDOW_COLORS.get(window_key, _WINDOW_COLORS["test"])
    n = len(tweets)

    # Banner
    banner = f"""
    <div style="background:{color};padding:20px 24px;border-radius:8px 8px 0 0;text-align:center">
      <div style="color:#fff;font-size:22px;font-weight:700;letter-spacing:0.5px">
        𝕏 {emoji} {window_name} &nbsp;|&nbsp; {date_str} &nbsp;|&nbsp; 🧵{n}
      </div>
    </div>"""

    # Tier stats
    tickers_in_post = [a["ticker"] for a in announcements if a.get("ticker")]
    scores_row = " &nbsp;|&nbsp; ".join(
        f'<strong style="color:{color}">{a["ticker"]}</strong> '
        f'<span style="color:#6b7280">{a["analysis_score"]:.0f}pkt</span>'
        for a in announcements
    )
    tier_block = f"""
    <div style="background:#f8fafc;padding:10px 24px;border-bottom:1px solid #e2e8f0;font-size:13px">
      🎯 Selekcja: {scores_row}
      &nbsp;&nbsp;{_supervisor_badge(validation.approved)}
    </div>"""

    # Issues (if any)
    issues_block = ""
    if validation.issues:
        items = "".join(f"<li>{i}</li>" for i in validation.issues)
        issues_block = f"""
    <div style="background:#fef3c7;padding:12px 24px;border-left:4px solid #f59e0b;font-size:13px">
      <strong>Problemy supervisora:</strong><ul style="margin:4px 0 0 0;padding-left:18px">{items}</ul>
    </div>"""

    # Tweet blocks
    tweet_blocks = ""
    for i, tweet in enumerate(tweets, 1):
        label = {1: "Hook", n: "Closing"}.get(i, f"Spółka {i - 1}")
        header = (
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'margin-bottom:8px">'
            f'<span style="font-size:12px;color:#94a3b8;font-weight:600;text-transform:uppercase;'
            f'letter-spacing:0.5px">𝕏 Tweet {i}/{n} — {label}</span>'
            f"{_char_badge(len(tweet))}"
            f"</div>"
        )
        body_html = (
            f'<div style="font-size:15px;line-height:1.6;color:#1e293b">'
            f"{_format_tweet_html(tweet)}"
            f"</div>"
        )
        tweet_blocks += f"""
    <div style="padding:16px 24px;border-left:4px solid {color};margin:0;border-bottom:1px solid #f1f5f9">
      {header}{body_html}
    </div>"""

    # Footer
    footer = f"""
    <div style="padding:16px 24px;background:#f8fafc;border-radius:0 0 8px 8px;font-size:12px;color:#94a3b8;text-align:center">
      puls-gpw &nbsp;·&nbsp; manual verification 2.4 &nbsp;·&nbsp; {date_str}<br>
      Nie jest to rekomendacja inwestycyjna.
    </div>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:20px;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
  <div style="max-width:620px;margin:0 auto;background:#fff;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.08)">
    {banner}
    {tier_block}
    {issues_block}
    {tweet_blocks}
    {footer}
  </div>
</body></html>"""

# ── Send email ────────────────────────────────────────────────────────────────

html_body = build_html(post.tweets, announcements, validation, window_key="wieczor", date_str=date_str)

host = os.environ["SMTP_HOST"]
port = int(os.environ["SMTP_PORT"])
user = os.environ["SMTP_USER"]
password = os.environ["SMTP_PASSWORD"]
owner = os.environ["OWNER_EMAIL"]

sup_icon = "✅" if validation.approved else "❌"
subject = f"[puls-gpw] 𝕏 🌆 Wieczór 🧵{len(post.tweets)} {sup_icon} | {date_str}"

msg = MIMEMultipart("alternative")
msg["Subject"] = subject
msg["From"] = user
msg["To"] = owner
msg.attach(MIMEText(html_body, "html", "utf-8"))

with smtplib.SMTP(host, port, timeout=10) as smtp:
    smtp.starttls(context=ssl.create_default_context())
    smtp.login(user, password)
    smtp.send_message(msg)

print(f"\nEmail sent to {owner}")
print(f"Subject: {subject}")
