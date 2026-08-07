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


def _verification_html(verify_link: str, origin: str) -> str:
    # Same defense-in-depth as _password_reset_html: origin derives from request
    # headers and the link from Firebase — escape both so no crafted value can
    # break out of an HTML attribute (AI-sec finding, PR #159). quote=True covers ".
    logo_url = _html_escape(f"{origin}/static/img/faro-mark.png", quote=True)
    verify_link = _html_escape(verify_link, quote=True)
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
    Dziękujemy za założenie konta w Faro. Twoje konto jest nieaktywne, dopóki
    nie potwierdzisz adresu e-mail. Kliknij poniższy przycisk, aby dokończyć
    rejestrację:
  </p>
  <p style="text-align:center;margin:0 0 20px;">
    <a href="{verify_link}" style="display:inline-block;background:#b8964f;color:#ffffff;padding:12px 28px;border-radius:6px;font-size:15px;font-weight:700;text-decoration:none;">Potwierdź e-mail</a>
  </p>
  <p style="font-size:13px;line-height:1.6;color:#6b7280;margin:0 0 8px;">
    Jeśli przycisk nie działa, skopiuj ten link do przeglądarki:<br>
    <a href="{verify_link}" style="color:#8a6d23;word-break:break-all;">{verify_link}</a>
  </p>
  <p style="font-size:13px;line-height:1.6;color:#6b7280;margin:0;">
    Jeśli to nie Ty zakładałeś(-aś) konto w Faro, zignoruj tę wiadomość —
    konto pozostanie nieaktywne.
  </p>
</div>

<div style="background:#f9fafb;border:1px solid #e5e7eb;border-top:none;padding:12px 18px;border-radius:0 0 8px 8px;font-size:12px;color:#6b7280;text-align:center;">
  Faro — jasne treści komunikatów ESPI/EBI. Wiadomość wysłana automatycznie.
</div>

</div>
</body>
</html>"""


def send_verification_email(to_email: str, verify_link: str, origin: str) -> None:
    """PUL-86: Faro-branded e-mail-verification message (Polish) via own SMTP.

    Raises on SMTP failure — the caller runs in a background task and maps the
    failure to a silent owner alert; the resend endpoint is the recovery path.
    """
    _send(
        "Faro — potwierdź adres e-mail",
        _verification_html(verify_link, origin),
        html=True,
        to=to_email,
        from_name="Faro",
    )


def _pl_announcements_plural(n: int) -> str:
    """Polish plural of 'komunikat' for a count (1 komunikat / 2-4 komunikaty / komunikatów)."""
    if n == 1:
        return "nowy komunikat"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "nowe komunikaty"
    return "nowych komunikatów"


def _event_type_label(code: str | None) -> str:
    """Humanize an event_type code for display (e.g. 'wyniki_finansowe' → 'Wyniki finansowe')."""
    if not code:
        return ""
    return str(code).replace("_", " ").strip().capitalize()


def _announcement_digest_html(items: list[dict], base_url: str) -> str:
    """Faro-branded digest listing a user's new watched-company announcements.

    Every embedded field is HTML-escaped (defense-in-depth, PR #159); each entry
    links to the user's watchlist hub (?view=my-wallet) — the announcement's
    company + title are already in the body, so the link just pulls the user in.
    """
    logo_url = _html_escape(f"{base_url}/static/img/faro-mark.png", quote=True)
    link = _html_escape(f"{base_url}/?view=my-wallet", quote=True)
    rows = []
    for it in items:
        company = _html_escape(str(it.get("company") or it.get("ticker") or ""), quote=True)
        ticker = _html_escape(str(it.get("ticker") or ""), quote=True)
        title = _html_escape(str(it.get("title") or ""), quote=True)
        event_type = _html_escape(_event_type_label(it.get("event_type")), quote=True)
        rows.append(f"""
  <div style="border:1px solid #e5e7eb;border-radius:6px;padding:14px 16px;margin:0 0 10px;">
    <div style="font-size:15px;font-weight:700;color:#14304A;margin:0 0 4px;">{company} <span style="color:#6b7280;font-weight:400;">({ticker})</span></div>
    <div style="font-size:14px;color:#111827;margin:0 0 6px;">{title}</div>
    <div style="font-size:12px;color:#6b7280;margin:0 0 10px;">{event_type}</div>
    <a href="{link}" style="display:inline-block;background:#b8964f;color:#ffffff;padding:8px 18px;border-radius:6px;font-size:13px;font-weight:700;text-decoration:none;">Zobacz w Faro</a>
  </div>""")
    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:20px;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<div style="max-width:520px;margin:0 auto;">

<div style="background:#14304A;color:#ffffff;padding:18px 24px;border-radius:8px 8px 0 0;">
  <span style="display:inline-block;background:#ffffff;border-radius:8px;padding:5px 7px;vertical-align:middle;"><img src="{logo_url}" alt="Faro" height="28" style="display:block;height:28px;"></span>
  <span style="font-size:20px;font-weight:700;vertical-align:middle;margin-left:10px;">Faro</span>
</div>

<div style="background:#ffffff;padding:24px;">
  <p style="font-size:15px;line-height:1.6;color:#111827;margin:0 0 16px;">
    Nowe komunikaty Twoich obserwowanych spółek:
  </p>
  {''.join(rows)}
</div>

<div style="background:#f9fafb;border:1px solid #e5e7eb;border-top:none;padding:12px 18px;border-radius:0 0 8px 8px;font-size:12px;color:#6b7280;text-align:center;">
  Faro — jasne treści komunikatów ESPI/EBI. Powiadomienia możesz wyłączyć w Ustawieniach.
</div>

</div>
</body>
</html>"""


def send_announcement_digest_email(to_email: str, items: list[dict], base_url: str) -> None:
    """Send a user their digest of new watched-company announcements via own SMTP.

    Raises on SMTP failure — the delivery job catches per-recipient and retries
    next pass (the pair is not recorded in the sent-log).
    """
    n = len(items)
    subject = f"Faro — {n} {_pl_announcements_plural(n)} Twoich spółek"
    _send(
        subject,
        _announcement_digest_html(items, base_url),
        html=True,
        to=to_email,
        from_name="Faro",
    )


def _pln(value: float | None) -> str:
    """Polish money formatting: comma decimal, thin-space thousands, `zł` suffix."""
    if value is None:
        return "—"
    # Credits cancel gross almost exactly, leaving values like -0.00004 that
    # would render as "-0,00 zł" and read as a bug. At two decimals they are zero.
    if abs(value) < 0.005:
        value = 0.0
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " zł"


def _tokens(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}".replace(",", " ")


def _cost_report_subject(summary: dict) -> str:
    """The subject carries the verdict, because a flagged day that reads like a
    calm one in the inbox defeats the point of a daily cadence."""
    day = summary.get("report_date")
    gross = _pln(summary.get("day_gross"))
    if summary.get("is_anomaly") and summary.get("ratio") is not None:
        ratio = f"{summary['ratio']:.1f}".replace(".", ",")
        return f"[puls-gpw] ⚠️ Koszty {day} — {gross}, {ratio}× mediany"
    return f"[puls-gpw] Koszty {day} — {gross}"


def _cost_report_html(
    summary: dict, services: list[dict], models: list[dict], base_url: str
) -> str:
    """Faro-branded daily GCP cost report (PUL-125).

    Takes primitives rather than the `CostReport` dataclass on purpose: this
    module imports only stdlib — no `src.*`, no `db.*` — and every other sender
    here takes `str` / `list[dict]`. The caller unpacks.

    Three things the numbers cannot say for themselves are said in prose, because
    a reader who infers the wrong thing from them will infer something reassuring:

    * **The previous day is provisional.** Billing rows keep landing for a day or
      two; at 09:00 the day holds 85-100 % of what it will finally cost.
    * **Net is not "what we pay forever".** A promotional credit currently cancels
      the whole bill, and the export does not carry the credit's remaining balance
      or expiry — that lives in the console and needs a human (PUL-69).
    * **A short baseline is not a calm day.** With fewer than four days of history
      the flag is suppressed, which in the inbox is indistinguishable from nothing
      being wrong unless the mail says so.

    Every interpolated value is escaped (defense-in-depth, PR #159).
    """
    logo_url = _html_escape(f"{base_url}/static/img/faro-mark.png", quote=True)

    service_rows = "".join(
        f"""
    <tr>
      <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6;">{_html_escape(str(s.get('name') or ''), quote=True)}</td>
      <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6;text-align:right;">{_html_escape(_pln(s.get('gross')), quote=True)}</td>
      <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6;text-align:right;color:#6b7280;">{_html_escape(_pln(s.get('net')), quote=True)}</td>
    </tr>"""
        for s in services
    )

    model_rows = "".join(
        f"""
    <tr>
      <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6;">{_html_escape(str(m.get('model') or ''), quote=True)}</td>
      <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6;text-align:right;">{_html_escape(_pln(m.get('gross')), quote=True)}</td>
      <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6;text-align:right;color:#6b7280;">{_html_escape(_tokens(m.get('input_tokens')), quote=True)}</td>
      <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6;text-align:right;color:#6b7280;">{_html_escape(_tokens(m.get('output_tokens')), quote=True)}</td>
    </tr>"""
        for m in models
    )

    if summary.get("median_7d") is None:
        baseline = (
            f"""<p style="font-size:13px;color:#92400e;background:#fffbeb;border:1px solid #fde68a;"""
            f"""border-radius:6px;padding:10px 12px;margin:0 0 16px;">"""
            f"""Baza porównawcza wciąż się buduje — mam {_html_escape(str(summary.get('baseline_days', 0)))} """
            f"""z 4 wymaganych dni historii, więc dziś nic nie jest oznaczane jako anomalia.</p>"""
        )
    else:
        ratio = summary.get("ratio")
        ratio_txt = f"{ratio:.1f}".replace(".", ",") if ratio is not None else "—"
        baseline = (
            f"""<p style="font-size:13px;color:#6b7280;margin:0 0 16px;">"""
            f"""Mediana z 7 dni: <strong>{_html_escape(_pln(summary.get('median_7d')), quote=True)}</strong> """
            f"""· stosunek: <strong>{_html_escape(ratio_txt)}×</strong></p>"""
        )

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:20px;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<div style="max-width:560px;margin:0 auto;">

<div style="background:#14304A;color:#ffffff;padding:18px 24px;border-radius:8px 8px 0 0;">
  <span style="display:inline-block;background:#ffffff;border-radius:8px;padding:5px 7px;vertical-align:middle;"><img src="{logo_url}" alt="Faro" height="28" style="display:block;height:28px;"></span>
  <span style="font-size:20px;font-weight:700;vertical-align:middle;margin-left:10px;">Koszty GCP</span>
</div>

<div style="background:#ffffff;padding:24px;">

  <p style="font-size:15px;line-height:1.6;color:#111827;margin:0 0 4px;">
    <strong>{_html_escape(str(summary.get('report_date') or ''), quote=True)}</strong> — brutto
    <strong>{_html_escape(_pln(summary.get('day_gross')), quote=True)}</strong>,
    po kredytach {_html_escape(_pln(summary.get('day_net')), quote=True)}
  </p>
  <p style="font-size:12px;color:#6b7280;margin:0 0 16px;">
    Dane za wczoraj są <strong>prowizoryczne</strong> — rozliczenia dopływają jeszcze przez dobę lub dwie,
    więc kwota może jeszcze urosnąć.
  </p>

  {baseline}

  <p style="font-size:15px;color:#111827;margin:0 0 6px;font-weight:700;">Wg usługi</p>
  <table style="width:100%;border-collapse:collapse;font-size:13px;margin:0 0 20px;">
    <tr style="background:#f9fafb;">
      <th style="padding:6px 10px;text-align:left;color:#6b7280;font-weight:600;">Usługa</th>
      <th style="padding:6px 10px;text-align:right;color:#6b7280;font-weight:600;">Brutto</th>
      <th style="padding:6px 10px;text-align:right;color:#6b7280;font-weight:600;">Po kredytach</th>
    </tr>{service_rows}
  </table>

  <p style="font-size:15px;color:#111827;margin:0 0 6px;font-weight:700;">Vertex AI wg modelu</p>
  <table style="width:100%;border-collapse:collapse;font-size:13px;margin:0 0 20px;">
    <tr style="background:#f9fafb;">
      <th style="padding:6px 10px;text-align:left;color:#6b7280;font-weight:600;">Model</th>
      <th style="padding:6px 10px;text-align:right;color:#6b7280;font-weight:600;">Brutto</th>
      <th style="padding:6px 10px;text-align:right;color:#6b7280;font-weight:600;">Tokeny wej.</th>
      <th style="padding:6px 10px;text-align:right;color:#6b7280;font-weight:600;">Tokeny wyj.</th>
    </tr>{model_rows}
  </table>

  <p style="font-size:14px;color:#111827;margin:0 0 16px;">
    Od początku miesiąca: <strong>{_html_escape(_pln(summary.get('mtd_gross')), quote=True)}</strong> brutto,
    {_html_escape(_pln(summary.get('mtd_net')), quote=True)} po kredytach.
  </p>

  <p style="font-size:12px;color:#6b7280;margin:0;">
    Kredyt promocyjny wciąż pokrywa rachunek, dlatego kwota „po kredytach" jest bliska zeru.
    Ile kredytu zostało i kiedy wygasa — tego eksport rozliczeń nie zawiera; to trzeba sprawdzić
    ręcznie w konsoli (Billing → Credits).
  </p>

</div>

<div style="background:#f9fafb;border:1px solid #e5e7eb;border-top:none;padding:12px 18px;border-radius:0 0 8px 8px;font-size:12px;color:#6b7280;text-align:center;">
  Faro — dzienny raport kosztów GCP.
</div>

</div>
</body>
</html>"""


def send_cost_report_email(
    summary: dict, services: list[dict], models: list[dict], base_url: str
) -> None:
    """Email the owner the daily GCP cost report (PUL-125).

    Raises on SMTP failure — the job catches it, alerts, and exits non-zero.
    """
    _send(_cost_report_subject(summary), _cost_report_html(summary, services, models, base_url), html=True)


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
