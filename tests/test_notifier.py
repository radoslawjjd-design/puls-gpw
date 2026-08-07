from datetime import date
from unittest.mock import patch


_ITEMS = [
    {"company": "Toya SA", "ticker": "TOA", "title": "Wyniki Q2", "event_type": "wyniki_finansowe"},
    {"company": "CD Projekt", "ticker": "CDR", "title": "Umowa", "event_type": "umowa"},
]


def test_send_announcement_digest_email_uses_send_with_faro_from_and_recipient():
    """The digest sender delegates to _send as user-facing Faro HTML mail, with a
    subject that reflects how many announcements are bundled."""
    from src.notifier import send_announcement_digest_email

    with patch("src.notifier._send") as send:
        send_announcement_digest_email("user@example.com", _ITEMS, "https://gpw.okiem.ai")

    assert send.call_count == 1
    args, kwargs = send.call_args
    subject = args[0]
    assert kwargs.get("html") is True
    assert kwargs.get("to") == "user@example.com"
    assert kwargs.get("from_name") == "Faro"
    assert "2" in subject  # two announcements bundled


def test_announcement_digest_html_escapes_fields_and_links_to_watchlist():
    """Every embedded field is HTML-escaped (PR #159) and each entry links to the
    user's watchlist hub (?view=my-wallet), not a ticker-filtered announcements list."""
    from src.notifier import _announcement_digest_html

    hostile = [{"company": "Evil <b>Co</b>", "ticker": "TOA", "title": "A & B <script>",
                "event_type": "inne"}]
    html = _announcement_digest_html(hostile, "https://gpw.okiem.ai")

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp;" in html  # "A & B" escaped
    assert "?view=my-wallet" in html
    assert "?view=announcements" not in html  # old ticker-filtered link is gone


def test_announcement_digest_humanizes_event_type():
    """event_type codes render as friendly labels, not raw underscored codes."""
    from src.notifier import _announcement_digest_html

    html = _announcement_digest_html(
        [{"company": "Toya SA", "ticker": "TOA", "title": "x", "event_type": "wyniki_finansowe"}],
        "https://gpw.okiem.ai",
    )
    assert "Wyniki finansowe" in html
    assert "wyniki_finansowe" not in html


# ── Daily cost report (PUL-125) ───────────────────────────────────────────────
#
# The mailer takes primitives, never the CostReport dataclass: src/notifier.py
# imports only stdlib today — zero src.*, zero db.* — and all six existing
# senders take str / list[dict] / list[str]. The caller unpacks, not the mailer.

_SUMMARY = {
    "report_date": date(2026, 8, 5),
    "day_gross": 2.9019,
    "day_net": -0.0001,
    "mtd_gross": 6.7679,
    "mtd_net": -0.0004,
    "median_7d": 1.2929,
    "ratio": 2.2446,
    "is_anomaly": True,
    "baseline_days": 7,
}

_SERVICES = [
    {"name": "Vertex AI", "gross": 2.0112, "net": -0.0000},
    {"name": "Cloud Run", "gross": 0.7346, "net": 0.0000},
]

_MODELS = [
    {"model": "gemini-2.5-flash", "gross": 1.8845,
     "input_tokens": 1065040.0, "output_tokens": 70538.0},
    {"model": "gemini-2.5-flash-lite", "gross": 0.1267,
     "input_tokens": 283548.0, "output_tokens": 12485.0},
]


def test_cost_report_sender_mails_the_owner_as_html():
    """Owner mail convention: html=True, no explicit recipient, no display name."""
    from src.notifier import send_cost_report_email

    with patch("src.notifier._send") as send:
        send_cost_report_email(_SUMMARY, _SERVICES, _MODELS, "https://gpw.okiem.ai")

    assert send.call_count == 1
    args, kwargs = send.call_args
    assert kwargs.get("html") is True
    assert kwargs.get("to") is None
    assert kwargs.get("from_name") is None
    assert "[puls-gpw]" in args[0]
    assert "2026-08-05" in args[0]


def test_anomaly_subject_differs_from_the_normal_one_and_names_the_ratio():
    """The subject alone has to answer 'how much worse' — nobody opens a mail to find out."""
    from src.notifier import send_cost_report_email

    calm = {**_SUMMARY, "is_anomaly": False, "ratio": 1.1}

    with patch("src.notifier._send") as send:
        send_cost_report_email(_SUMMARY, _SERVICES, _MODELS, "https://gpw.okiem.ai")
        anomaly_subject = send.call_args[0][0]
        send_cost_report_email(calm, _SERVICES, _MODELS, "https://gpw.okiem.ai")
        calm_subject = send.call_args[0][0]

    assert anomaly_subject != calm_subject
    assert "2,2" in anomaly_subject          # the ratio, Polish decimal comma
    assert "2,2" not in calm_subject


def test_short_baseline_says_so_and_names_the_day_count():
    """A suppressed flag reads identically to a calm day unless the mail says the baseline is short."""
    from src.notifier import _cost_report_html

    short = {**_SUMMARY, "median_7d": None, "ratio": None,
             "is_anomaly": False, "baseline_days": 3}

    html = _cost_report_html(short, _SERVICES, _MODELS, "https://gpw.okiem.ai")

    assert "3" in html
    assert "buduje" in html.lower()


def test_cost_report_escapes_a_hostile_service_name():
    """Service names come from Google, but escaping is defense-in-depth here as everywhere (PR #159)."""
    from src.notifier import _cost_report_html

    hostile = [{"name": "Eve <script>alert(1)</script> & Co", "gross": 1.0, "net": 0.0}]

    html = _cost_report_html(_SUMMARY, hostile, _MODELS, "https://gpw.okiem.ai")

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp;" in html


def test_token_column_is_labelled_tokens_not_requests():
    """usage.unit reads 'requests' but carries token counts — Google's naming, not ours."""
    from src.notifier import _cost_report_html

    html = _cost_report_html(_SUMMARY, _SERVICES, _MODELS, "https://gpw.okiem.ai")

    assert "token" in html.lower()
    assert "requests" not in html.lower()


def test_cost_report_states_what_the_numbers_cannot_say():
    """Two caveats the reader cannot infer: D-1 is still settling, and credit left is not in the export."""
    from src.notifier import _cost_report_html

    html = _cost_report_html(_SUMMARY, _SERVICES, _MODELS, "https://gpw.okiem.ai")

    assert "prowizor" in html.lower()
    assert "kredyt" in html.lower()
    assert "6,77" in html          # month-to-date gross, Polish decimal comma
