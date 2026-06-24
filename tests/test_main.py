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
