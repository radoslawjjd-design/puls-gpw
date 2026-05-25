"""
SMTP email notifier.

send_xpost(ann, xpost, score) — nowe ogłoszenie z X-postem
send_error(subject, body)     — alert o błędzie pipeline
"""
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import OWNER_EMAIL, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER

logger = logging.getLogger(__name__)


def send_xpost(ann: dict, xpost: str, score: int) -> bool:
    """
    Wysyła email z X-postem dla nowego ogłoszenia ESPI/EBI.
    Zwraca True jeśli wysłano pomyślnie.
    """
    company  = ann.get("company", "?")
    title    = ann.get("title", "")
    bankier  = ann.get("bankier_url", "")
    pub_time = ann.get("pub_time")
    time_str = pub_time.strftime("%H:%M") if pub_time else "?"

    subject = f"[GPW] {company} — {title[:60]}"

    html = _render_html(company, title, xpost, score, bankier, time_str)
    text = _render_text(company, title, xpost, score, bankier, time_str)

    return _send(subject, html, text)


def send_error(subject: str, body: str) -> bool:
    """Wysyła alert email o błędzie pipeline."""
    html = f"<pre>{body}</pre>"
    return _send(f"[puls-gpw ERROR] {subject}", html, body)


def _render_html(
    company:  str,
    title:    str,
    xpost:    str,
    score:    int,
    url:      str,
    time_str: str,
) -> str:
    # $TICKER → pogrubiony cashtag
    formatted_post = _format_cashtags(xpost)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:16px">
  <p style="color:#666;font-size:13px;margin-bottom:4px">
    GPW ESPI/EBI — {company} &bull; {time_str}
  </p>
  <h2 style="font-size:16px;margin:0 0 12px">{title}</h2>

  <div style="background:#f8f8f8;border-left:3px solid #1da1f2;padding:12px 16px;
              border-radius:4px;white-space:pre-wrap;font-size:15px;line-height:1.5">
{formatted_post}
  </div>

  <p style="margin-top:12px;font-size:12px;color:#999">
    Supervisor score: {score}/10 &bull;
    <a href="{url}">Bankier</a>
  </p>
</body>
</html>"""


def _render_text(
    company:  str,
    title:    str,
    xpost:    str,
    score:    int,
    url:      str,
    time_str: str,
) -> str:
    return f"{company} ({time_str})\n{title}\n\n{xpost}\n\nScore: {score}/10\n{url}"


def _format_cashtags(text: str) -> str:
    return text.replace("$", "<strong>$").replace("\n", "<br>\n")


def _send(subject: str, html: str, text: str) -> bool:
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD, OWNER_EMAIL]):
        logger.warning("SMTP nie skonfigurowany — pomijam wysyłkę emaila")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = OWNER_EMAIL

    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html",  "utf-8"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, OWNER_EMAIL, msg.as_string())
        logger.info(f"Email wysłany: {subject[:60]}")
        return True
    except Exception as e:
        logger.error(f"Błąd SMTP: {e}")
        return False
