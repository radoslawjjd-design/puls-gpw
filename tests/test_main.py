"""Unit tests for main.py pipeline orchestration — best-effort upsert_company wiring.

All BigQuery/scraper/analyzer/parser side effects are mocked; no network, no real creds.
"""
import datetime
from unittest.mock import MagicMock

import pytest

import main
from src.analyzer import AnalysisResult
from src.exceptions import BigQueryError
from src.parser import ParsedContent
from src.scraper import Announcement

_ANN = Announcement(
    title="Test announcement",
    espi_code="1/2026",
    bankier_url="https://example.com/ann/1",
    published_at=datetime.datetime(2026, 6, 23, 12, 0, tzinfo=datetime.timezone.utc),
    source="ESPI",
    priority="normal",
)

_PARSED = ParsedContent(
    announcement_id="abc123",
    parsed_content="treść komunikatu",
    ticker="ECH",
    company="Echo Investment SA",
    hop_url="https://www.bankier.pl/inwestowanie/profile/quote.html?symbol=ECHO",
    isin="PLECHPS00019",
)

_ANALYSIS = AnalysisResult(
    announcement_id="abc123",
    structured_analysis='{"company": "Echo Investment SA"}',
    analysis_approved=True,
    analysis_reject_reason=None,
    event_type="inne",
    analysis_score=10.0,
)


@pytest.fixture
def pipeline_mocks(monkeypatch):
    """Patch every main.py collaborator with a happy-path mock; return them for override."""
    mocks = {
        "create_table_if_not_exists": MagicMock(),
        "ensure_schema_current": MagicMock(),
        "create_x_posts_table_if_not_exists": MagicMock(),
        "create_companies_table_if_not_exists": MagicMock(),
        "ensure_companies_schema_current": MagicMock(),
        "scrape_new_announcements": MagicMock(return_value=[_ANN]),
        "announcement_id_for_url": MagicMock(return_value="abc123"),
        "parse_announcement": MagicMock(return_value=_PARSED),
        "insert_announcement": MagicMock(),
        "update_parsed_content": MagicMock(),
        "upsert_company": MagicMock(),
        "analyze_announcement": MagicMock(return_value=_ANALYSIS),
        "save_analysis_result": MagicMock(),
        "send_alert": MagicMock(),
        # Event-driven notification hook collaborators (Phase 2).
        "create_notification_sent_log_table_if_not_exists": MagicMock(),
        "ensure_notification_sent_log_schema_current": MagicMock(),
        "select_recipients_for_announcement": MagicMock(return_value=[]),
        "send_announcement_digest_email": MagicMock(),
        "record_notification_sent": MagicMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(main, name, mock)
    monkeypatch.setattr(main.sys, "argv", ["main.py"])
    return mocks


def test_upsert_company_failure_does_not_abort_pipeline(pipeline_mocks, monkeypatch):
    """A BigQueryError from upsert_company must be swallowed — analysis/alerting still runs."""
    pipeline_mocks["upsert_company"].side_effect = BigQueryError("boom")
    monkeypatch.setattr(main.sys, "exit", MagicMock())

    main.main()

    pipeline_mocks["analyze_announcement"].assert_called_once()
    pipeline_mocks["save_analysis_result"].assert_called_once()
    pipeline_mocks["send_alert"].assert_not_called()
    main.sys.exit.assert_not_called()


def test_happy_path_upserts_company(pipeline_mocks):
    main.main()

    pipeline_mocks["upsert_company"].assert_called_once_with(
        _PARSED.ticker, _PARSED.company, _PARSED.hop_url, _PARSED.isin
    )
    pipeline_mocks["send_alert"].assert_not_called()


def test_notification_hook_emails_each_recipient_and_records(pipeline_mocks):
    """An approved+scored announcement emails every opted-in watcher once and
    records each (user, announcement) pair in the sent-log."""
    pipeline_mocks["select_recipients_for_announcement"].return_value = [
        {"user_id": "u1", "email": "a@example.com"},
        {"user_id": "u2", "email": "b@example.com"},
    ]

    main.main()

    pipeline_mocks["select_recipients_for_announcement"].assert_called_once_with("abc123")
    assert pipeline_mocks["send_announcement_digest_email"].call_count == 2
    assert pipeline_mocks["record_notification_sent"].call_count == 2
    recorded = {c.args[0] for c in pipeline_mocks["record_notification_sent"].call_args_list}
    assert recorded == {"u1", "u2"}
    # Each send carries a single-item digest built from the ingestion locals.
    to_addrs = {c.args[0] for c in pipeline_mocks["send_announcement_digest_email"].call_args_list}
    assert to_addrs == {"a@example.com", "b@example.com"}
    pipeline_mocks["send_alert"].assert_not_called()


def test_notification_hook_skipped_for_rejected_announcement(pipeline_mocks):
    """A rejected (or unscored) announcement never triggers the recipient query or a send."""
    pipeline_mocks["analyze_announcement"].return_value = AnalysisResult(
        announcement_id="abc123",
        structured_analysis="{}",
        analysis_approved=False,
        analysis_reject_reason="not material",
        event_type="inne",
        analysis_score=None,
    )

    main.main()

    pipeline_mocks["select_recipients_for_announcement"].assert_not_called()
    pipeline_mocks["send_announcement_digest_email"].assert_not_called()
    pipeline_mocks["record_notification_sent"].assert_not_called()
    pipeline_mocks["send_alert"].assert_not_called()


def test_notification_send_failure_isolated_and_alerts(pipeline_mocks, monkeypatch):
    """A recipient whose send keeps failing is skipped (permanently missed) without
    aborting the run; other recipients still get mailed and an owner alert fires once."""
    monkeypatch.setattr(main.sys, "exit", MagicMock())
    monkeypatch.setattr(main.time, "sleep", MagicMock())  # no real backoff in tests
    pipeline_mocks["select_recipients_for_announcement"].return_value = [
        {"user_id": "bad", "email": "bad@example.com"},
        {"user_id": "good", "email": "good@example.com"},
    ]

    def _send(to_email, items, base_url):
        if to_email == "bad@example.com":
            raise RuntimeError("smtp down")

    pipeline_mocks["send_announcement_digest_email"].side_effect = _send

    main.main()

    # The healthy recipient is still delivered + recorded.
    pipeline_mocks["record_notification_sent"].assert_called_once()
    assert pipeline_mocks["record_notification_sent"].call_args.args[0] == "good"
    # The failed recipient is never recorded, and the run never aborts.
    main.sys.exit.assert_not_called()
    # A single owner alert summarizes the notification failure(s).
    pipeline_mocks["send_alert"].assert_called_once()


def test_notification_recipient_query_error_does_not_abort_ingestion(pipeline_mocks, monkeypatch):
    """A BigQueryError from the recipient query is contained in the hook — ingestion
    completes and the run is not fatal (send_alert may fire, but never sys.exit)."""
    monkeypatch.setattr(main.sys, "exit", MagicMock())
    pipeline_mocks["select_recipients_for_announcement"].side_effect = BigQueryError("boom")

    main.main()

    # Ingestion itself completed for this announcement.
    pipeline_mocks["save_analysis_result"].assert_called_once()
    pipeline_mocks["send_announcement_digest_email"].assert_not_called()
    main.sys.exit.assert_not_called()
