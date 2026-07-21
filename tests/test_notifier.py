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


def test_announcement_digest_html_escapes_fields_and_links_to_faro():
    """Every embedded field is HTML-escaped (PR #159) and each entry links to the
    Faro announcements view filtered by ticker."""
    from src.notifier import _announcement_digest_html

    hostile = [{"company": "Evil <b>Co</b>", "ticker": "TOA", "title": "A & B <script>",
                "event_type": "inne"}]
    html = _announcement_digest_html(hostile, "https://gpw.okiem.ai")

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp;" in html  # "A & B" escaped
    assert "?view=announcements&amp;ticker=TOA" in html or "?view=announcements&ticker=TOA" in html


def test_announcement_digest_humanizes_event_type():
    """event_type codes render as friendly labels, not raw underscored codes."""
    from src.notifier import _announcement_digest_html

    html = _announcement_digest_html(
        [{"company": "Toya SA", "ticker": "TOA", "title": "x", "event_type": "wyniki_finansowe"}],
        "https://gpw.okiem.ai",
    )
    assert "Wyniki finansowe" in html
    assert "wyniki_finansowe" not in html
