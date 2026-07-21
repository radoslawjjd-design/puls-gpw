"""Unit tests for notification_main.py — delivery-pass orchestration."""
from unittest.mock import MagicMock

import pytest

import notification_main
from db.bigquery import BigQueryError

_ROWS = [
    {"user_id": "u1", "email": "u1@example.com", "announcement_id": "a1",
     "ticker": "TOA", "company": "Toya SA", "title": "Wyniki", "event_type": "wyniki_finansowe"},
    {"user_id": "u1", "email": "u1@example.com", "announcement_id": "a2",
     "ticker": "CDR", "company": "CD Projekt", "title": "Umowa", "event_type": "umowa"},
    {"user_id": "u2", "email": "u2@example.com", "announcement_id": "a1",
     "ticker": "TOA", "company": "Toya SA", "title": "Wyniki", "event_type": "wyniki_finansowe"},
]


@pytest.fixture
def m(monkeypatch):
    mocks = {
        "create": MagicMock(name="create_notification_sent_log_table_if_not_exists"),
        "ensure": MagicMock(name="ensure_notification_sent_log_schema_current"),
        "select": MagicMock(name="select_pending_notifications", return_value=list(_ROWS)),
        "send": MagicMock(name="send_announcement_digest_email"),
        "record": MagicMock(name="record_notification_sent"),
        "alert": MagicMock(name="send_alert"),
    }
    monkeypatch.setattr(notification_main, "create_notification_sent_log_table_if_not_exists", mocks["create"])
    monkeypatch.setattr(notification_main, "ensure_notification_sent_log_schema_current", mocks["ensure"])
    monkeypatch.setattr(notification_main, "select_pending_notifications", mocks["select"])
    monkeypatch.setattr(notification_main, "send_announcement_digest_email", mocks["send"])
    monkeypatch.setattr(notification_main, "record_notification_sent", mocks["record"])
    monkeypatch.setattr(notification_main, "send_alert", mocks["alert"])
    monkeypatch.setattr(notification_main.sys, "argv", ["notification_main.py"])
    return mocks


def test_happy_path_one_digest_per_user_and_records_each_pair(m):
    """Each user gets exactly one digest of their items; every (user, announcement)
    pair is recorded; no owner alert."""
    notification_main.main()

    assert m["send"].call_count == 2  # u1, u2
    sent_to = {c.args[0] for c in m["send"].call_args_list}
    assert sent_to == {"u1@example.com", "u2@example.com"}
    assert m["record"].call_count == 3  # (u1,a1),(u1,a2),(u2,a1)
    m["alert"].assert_not_called()


def test_no_pending_sends_nothing(m):
    """Empty recipient set → no emails, no records, no alert, clean exit."""
    m["select"].return_value = []
    notification_main.main()

    m["send"].assert_not_called()
    m["record"].assert_not_called()
    m["alert"].assert_not_called()


def test_per_user_send_failure_does_not_block_others_and_alerts(m):
    """A send failure for one user is isolated: other users are still emailed, the
    failing user's pairs are NOT recorded (retry next pass), and the owner is
    alerted once — the pass does not exit non-zero."""
    def _send(email, items, base_url):
        if email == "u1@example.com":
            raise RuntimeError("smtp down")

    m["send"].side_effect = _send
    notification_main.main()  # must not raise SystemExit

    # u2 still delivered + recorded; u1's pairs not recorded
    recorded_users = {c.args[0] for c in m["record"].call_args_list}
    assert "u2" in recorded_users
    assert "u1" not in recorded_users
    m["alert"].assert_called_once()


def test_select_failure_alerts_and_exits_nonzero(m):
    """A failure of the initial recipient query is fatal: alert + exit 1."""
    m["select"].side_effect = BigQueryError("bq down")

    with pytest.raises(SystemExit) as exc:
        notification_main.main()
    assert exc.value.code == 1
    m["alert"].assert_called_once()
    m["send"].assert_not_called()
