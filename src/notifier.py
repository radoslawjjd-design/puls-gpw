"""Email alert delivery for pipeline failures."""
import os
import smtplib
import ssl
import traceback
from datetime import datetime, timezone
from email.mime.text import MIMEText


def send_alert(exc: BaseException) -> None:
    """Send a plain-text error alert email to the pipeline owner via SMTP/STARTTLS.

    Reads SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, OWNER_EMAIL from env.
    Does not catch exceptions — propagates to the caller, which logs and exits(1).
    """
    host = os.environ["SMTP_HOST"]
    port = int(os.environ["SMTP_PORT"])
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]
    owner = os.environ["OWNER_EMAIL"]

    timestamp = datetime.now(timezone.utc).isoformat()
    exc_type = type(exc).__name__
    body = (
        f"Pipeline failure at {timestamp}\n\n"
        f"Exception: {exc_type}: {exc}\n\n"
        f"Traceback:\n{traceback.format_exc()}"
    )

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"[puls-gpw] Pipeline ERROR: {exc_type}"
    msg["From"] = user
    msg["To"] = owner

    with smtplib.SMTP(host, port, timeout=10) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(user, password)
        smtp.send_message(msg)
