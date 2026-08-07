"""Unit tests for cost_report_main.py — entrypoint orchestration (PUL-125).

The four cases here are the four ways the 09:00 job can end, and three of them
are failures. That ratio is the point: `sys.exit(1)` reaches nobody — the jobs
run with `--max-retries=0` and the Cloud Monitoring alert policy has no
notification channel — so `send_alert` is the entire path from a broken job to
a human. A failure that does not alert is a failure that is invisible until
someone opens the console.

Collaborators are patched on the *importing* module, not at their source, per
`tests/test_company_stats_main.py`. The clock is left alone: the mocked reads
echo back the date range they were handed, so the tests never need to know what
day it is.
"""
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

import cost_report_main
from db.bigquery import BigQueryError


def _billing_row(day: date) -> dict:
    return {
        "day": day,
        "service": "Vertex AI",
        "sku": "Gemini 2.5 Flash GA Text Input - Predictions",
        "gross": 1.2143,
        "net": -0.0001,
        "usage_amount": 1065040.0,
        "usage_unit": "requests",
    }


@pytest.fixture
def mocks(monkeypatch):
    """Every collaborator mocked; reads echo the window they were asked for."""
    m = {
        "rows": MagicMock(name="get_billing_rows",
                          side_effect=lambda start, end: [_billing_row(end)]),
        "daily": MagicMock(name="get_daily_gross",
                           side_effect=lambda start, end: {
                               start + timedelta(days=i): 1.0 for i in range((end - start).days + 1)
                           }),
        "send": MagicMock(name="send_cost_report_email"),
        "alert": MagicMock(name="send_alert"),
    }
    names = {
        "rows": "get_billing_rows",
        "daily": "get_daily_gross",
        "send": "send_cost_report_email",
        "alert": "send_alert",
    }
    for key, attr in names.items():
        monkeypatch.setattr(cost_report_main, attr, m[key])
    return m


def test_happy_path_sends_one_report_and_does_not_alert(mocks):
    cost_report_main.main()

    assert mocks["send"].call_count == 1
    assert mocks["alert"].call_count == 0

    summary, services, models, base_url = mocks["send"].call_args[0]
    assert summary["day_gross"] == pytest.approx(1.2143)
    assert [s["name"] for s in services] == ["Vertex AI"]
    assert models and models[0]["model"] == "gemini-2.5-flash"
    assert base_url.startswith("https://")


def test_the_report_covers_yesterday_in_warsaw_not_today(mocks):
    """A report for today would always be near-empty — the export lags by hours.

    The expected date is computed here from the clock, not read back from
    `_report_date()`. Comparing that function against itself would pass just as
    happily if it returned today, or next week.
    """
    expected = datetime.now(ZoneInfo("Europe/Warsaw")).date() - timedelta(days=1)

    cost_report_main.main()

    summary = mocks["send"].call_args[0][0]
    assert summary["report_date"] == expected

    # And the fetches are anchored to that same day, not to the local date.
    assert mocks["rows"].call_args[0][1] == expected
    assert mocks["daily"].call_args[0][1] == expected


def test_a_query_failure_alerts_and_exits_non_zero(mocks):
    mocks["rows"].side_effect = BigQueryError("get_billing_rows failed: boom")

    with pytest.raises(SystemExit) as exit_info:
        cost_report_main.main()

    assert exit_info.value.code == 1
    assert mocks["alert"].call_count == 1
    assert mocks["send"].call_count == 0


def test_zero_billing_rows_alerts_and_sends_no_report(mocks):
    """No rows for the day means the query broke, not that the day was free."""
    mocks["rows"].side_effect = lambda start, end: []

    with pytest.raises(SystemExit) as exit_info:
        cost_report_main.main()

    assert exit_info.value.code == 1
    assert mocks["alert"].call_count == 1
    assert mocks["send"].call_count == 0


def test_rows_that_miss_the_report_date_still_count_as_zero(mocks):
    """Month-to-date rows can be non-empty while the day itself is absent — that is the real shape of the bug."""
    mocks["rows"].side_effect = lambda start, end: [_billing_row(end - timedelta(days=3))]

    with pytest.raises(SystemExit) as exit_info:
        cost_report_main.main()

    assert exit_info.value.code == 1
    assert mocks["send"].call_count == 0


def test_an_alert_that_itself_fails_is_logged_and_still_exits_non_zero(mocks):
    """The last thing standing must not swallow the exit code on its way down."""
    mocks["rows"].side_effect = BigQueryError("boom")
    mocks["alert"].side_effect = RuntimeError("SMTP down")

    with pytest.raises(SystemExit) as exit_info:
        cost_report_main.main()

    assert exit_info.value.code == 1


def test_a_send_failure_alerts_and_exits_non_zero(mocks):
    """The mail is the whole deliverable; a silent send failure is the job doing nothing."""
    mocks["send"].side_effect = RuntimeError("SMTP down")

    with pytest.raises(SystemExit) as exit_info:
        cost_report_main.main()

    assert exit_info.value.code == 1
    assert mocks["alert"].call_count == 1


def test_the_anomaly_factor_comes_from_the_environment(mocks, monkeypatch):
    """The pure modules take the factor as a parameter; this entry point is where the env is read."""
    monkeypatch.setenv("COST_ANOMALY_FACTOR", "0.5")
    cost_report_main.main()
    assert mocks["send"].call_args[0][0]["is_anomaly"] is True

    mocks["send"].reset_mock()
    monkeypatch.setenv("COST_ANOMALY_FACTOR", "100")
    cost_report_main.main()
    assert mocks["send"].call_args[0][0]["is_anomaly"] is False
