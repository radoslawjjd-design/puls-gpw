"""Cloud Run Job entrypoint for the daily GCP cost report (PUL-125)."""
import logging
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from src.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

from db.bigquery import get_billing_rows, get_daily_gross
from src.cost_report import DEFAULT_BASE_URL, build_report
from src.notifier import send_alert, send_cost_report_email

WARSAW = ZoneInfo("Europe/Warsaw")

# How far back the anomaly baseline reaches. Kept in step with the window
# src/cost_report.py medians over — this side decides what to *fetch*, that side
# decides what to *judge*, and a shorter fetch would silently starve the median.
_BASELINE_WINDOW_DAYS = 7


def _report_date() -> date:
    """Yesterday, in Warsaw.

    Today is never worth reporting: the export lags by hours, so a same-day
    report is mostly empty and would read as a quiet day rather than as missing
    data. Yesterday at 09:00 holds 85-100 % of its final cost, which is why the
    mail calls it provisional rather than final.
    """
    return datetime.now(WARSAW).date() - timedelta(days=1)


def main() -> None:
    try:
        report_date = _report_date()

        # The two environment reads the pure modules deliberately do not do.
        # src/cost_report.py stays importable without a configured environment,
        # which is what lets its decisions be unit-tested at all.
        anomaly_factor = float(os.environ.get("COST_ANOMALY_FACTOR", "2.0"))
        base_url = os.environ.get("APP_BASE_URL", DEFAULT_BASE_URL)

        # Two windows, two reads: the month-to-date figure cannot reach into the
        # previous month, and early in a month the baseline must.
        rows = get_billing_rows(report_date.replace(day=1), report_date)
        daily_gross = get_daily_gross(
            report_date - timedelta(days=_BASELINE_WINDOW_DAYS), report_date
        )

        # Raised from *inside* the try on purpose: send_alert reads the ambient
        # traceback rather than its argument, so an alert raised outside an
        # except block arrives saying "NoneType: None".
        #
        # The test is on the report date specifically, not on `rows` being
        # empty. Mid-month the month-to-date rows are non-empty even when the
        # day itself is missing, which is exactly the shape a broken query or a
        # stalled export takes.
        if not any(r.get("day") == report_date for r in rows):
            raise RuntimeError(
                f"no billing rows for {report_date} — the export or the query is broken; "
                f"refusing to send a report claiming the day cost nothing"
            )

        report = build_report(rows, daily_gross, report_date, anomaly_factor)

        # src/notifier.py imports only stdlib and takes primitives, so the
        # dataclass is unpacked here rather than reaching into the mailer.
        summary = {
            "report_date": report.report_date,
            "day_gross": report.day_gross,
            "day_net": report.day_net,
            "mtd_gross": report.mtd_gross,
            "mtd_net": report.mtd_net,
            "median_7d": report.median_7d,
            "ratio": report.ratio,
            "is_anomaly": report.is_anomaly,
            "baseline_days": report.baseline_days,
        }
        send_cost_report_email(summary, report.services, report.vertex_models, base_url)

        logger.info(
            "cost_report_main: done — date=%s gross=%.4f mtd=%.4f median=%s anomaly=%s",
            report.report_date, report.day_gross, report.mtd_gross,
            report.median_7d, report.is_anomaly,
        )

    except Exception as exc:
        logger.exception("cost_report_main: pipeline failed")
        try:
            send_alert(exc)
            logger.info("cost_report_main: alert email sent")
        except Exception as alert_exc:
            logger.error("cost_report_main: failed to send alert: %s", alert_exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
