"""Tests for the cost report domain logic (PUL-125 daily-cost-report).

Pure functions over the rows db/bigquery.py returns — no BigQuery, no SMTP, no
clock. Everything the report *decides* lives here, which is why the interesting
tests are here and not in the SQL layer.

The SKU strings below are copied verbatim from the live export
(`SELECT DISTINCT sku.description ... WHERE service.description = 'Vertex AI'`,
checked 2026-08-07). They are quoted exactly, leading whitespace included,
because every trap in this classifier is a string-matching trap:

* `"Gemini 2.5 Flash Lite ..."` contains `"Flash"`, so a Flash branch tested
  before the Lite branch silently folds two models into one row.
* Two SKUs really do start with a space. `startswith("Gemini")` drops them.
* Every prediction SKU ends in `- Predictions`, so `endswith("Output")` matches
  none of the output SKUs and understates output tokens to zero.

None of those failures look like failures — they produce a plausible table.
"""

from datetime import date

from src.cost_report import build_report, classify_sku, trailing_median

# Verbatim from the export. Do not tidy the leading spaces away.
SKU_GA_INPUT = "Gemini 2.5 Flash GA Text Input - Predictions"
SKU_GA_OUTPUT_THINKING = "Gemini 2.5 Flash GA Thinking Text Output - Predictions"
SKU_GA_OUTPUT_THINKING_ON = "Gemini 2.5 Flash GA Text Output (Thinking On) - Predictions"
SKU_GA_CACHING = " Gemini 2.5 Flash GA Input Text Caching"
SKU_LITE_INPUT = "Gemini 2.5 Flash Lite Text Input - Predictions"
SKU_LITE_OUTPUT = "Gemini 2.5 Flash Lite Text Output - Predictions"
SKU_LITE_CACHING = " Gemini 2.5 Flash Lite Input Text Caching"

FLASH = "gemini-2.5-flash"
LITE = "gemini-2.5-flash-lite"


def _row(day, service, sku, gross, net=None, usage=0.0):
    return {
        "day": day,
        "service": service,
        "sku": sku,
        "gross": gross,
        "net": net if net is not None else -0.0001,
        "usage_amount": usage,
        "usage_unit": "requests",
    }


def test_every_vertex_sku_in_the_export_classifies():
    """All seven live SKUs, each named verbatim — this is the test that catches all three traps."""
    assert classify_sku(SKU_GA_INPUT) == (FLASH, "input")
    assert classify_sku(SKU_GA_OUTPUT_THINKING) == (FLASH, "output")
    assert classify_sku(SKU_GA_OUTPUT_THINKING_ON) == (FLASH, "output")
    assert classify_sku(SKU_GA_CACHING) == (FLASH, "input")
    assert classify_sku(SKU_LITE_INPUT) == (LITE, "input")
    assert classify_sku(SKU_LITE_OUTPUT) == (LITE, "output")
    assert classify_sku(SKU_LITE_CACHING) == (LITE, "input")


def test_lite_and_ga_do_not_collapse_into_one_model():
    """'Flash Lite' contains 'Flash'; the Lite branch has to be evaluated first."""
    assert classify_sku(SKU_LITE_INPUT)[0] != classify_sku(SKU_GA_INPUT)[0]


def test_another_models_ga_sku_does_not_land_in_the_flash_row():
    """Matching on a bare "GA" would file this under Flash — and it would reconcile perfectly.

    That is the whole danger: the per-model rows would still sum to the Vertex AI
    service line, so the one check that catches dropped SKUs would pass while the
    table attributed another model's spend to Flash. Unrecognised models belong in
    "other", where they are visible.
    """
    assert classify_sku("Gemini 3 Pro GA Text Input - Predictions") is None
    assert classify_sku("Gemini 3 Pro Text Output - Predictions") is None


def test_non_vertex_sku_does_not_classify():
    """None is reserved for SKUs that are not a Gemini model line."""
    assert classify_sku("Jobs CPU in europe-central2") is None
    assert classify_sku("Active Logical Storage (europe-central2)") is None


def test_per_model_gross_sums_to_the_vertex_service_total():
    """An unclassified Vertex SKU lands in its own row rather than vanishing."""
    day = date(2026, 8, 5)
    rows = [
        _row(day, "Vertex AI", SKU_GA_INPUT, 1.2143, usage=1065040.0),
        _row(day, "Vertex AI", SKU_GA_OUTPUT_THINKING, 0.8, usage=90000.0),
        _row(day, "Vertex AI", SKU_LITE_INPUT, 0.3, usage=500000.0),
        _row(day, "Vertex AI", "Gemini 9.9 Ultra Text Input - Predictions", 0.05, usage=1234.0),
        _row(day, "Cloud Run", "Requests", 0.11),
    ]

    report = build_report(rows, {}, day, anomaly_factor=2.0)

    vertex_total = next(s["gross"] for s in report.services if s["name"] == "Vertex AI")
    assert round(sum(m["gross"] for m in report.vertex_models), 6) == round(vertex_total, 6)


def test_vertex_tokens_split_by_direction():
    """Caching is an input-side SKU, so its tokens belong to the model's input count."""
    day = date(2026, 8, 5)
    rows = [
        _row(day, "Vertex AI", SKU_GA_INPUT, 1.0, usage=1_000_000.0),
        _row(day, "Vertex AI", SKU_GA_CACHING, 0.01, usage=80_000.0),
        _row(day, "Vertex AI", SKU_GA_OUTPUT_THINKING_ON, 0.5, usage=60_000.0),
    ]

    report = build_report(rows, {}, day, anomaly_factor=2.0)

    flash = next(m for m in report.vertex_models if m["model"] == FLASH)
    assert flash["input_tokens"] == 1_080_000.0
    assert flash["output_tokens"] == 60_000.0


def test_median_over_fewer_than_four_days_returns_none():
    """Three days is not a baseline; a report must not flag against it."""
    daily = {date(2026, 8, 2): 1.0, date(2026, 8, 3): 1.0, date(2026, 8, 4): 1.0}

    assert trailing_median(daily, date(2026, 8, 5)) is None


def test_short_baseline_suppresses_the_anomaly_flag():
    day = date(2026, 8, 5)
    daily = {date(2026, 8, 2): 0.2, date(2026, 8, 3): 0.2, date(2026, 8, 4): 0.2, day: 99.0}
    rows = [_row(day, "Cloud Run", "Requests", 99.0)]

    report = build_report(rows, daily, day, anomaly_factor=2.0)

    assert report.median_7d is None
    assert report.is_anomaly is False
    assert report.baseline_days == 3


def test_median_excludes_the_report_day_itself():
    """The day under test must not sit in its own baseline, or a spike raises the bar it is judged against."""
    daily = {
        date(2026, 8, 1): 1.0,
        date(2026, 8, 2): 1.0,
        date(2026, 8, 3): 1.0,
        date(2026, 8, 4): 1.0,
        date(2026, 8, 5): 100.0,
    }

    assert trailing_median(daily, date(2026, 8, 5)) == 1.0


def test_a_day_exactly_at_the_factor_does_not_flag():
    day = date(2026, 8, 5)
    daily = {
        date(2026, 8, 1): 1.0,
        date(2026, 8, 2): 1.0,
        date(2026, 8, 3): 1.0,
        date(2026, 8, 4): 1.0,
        day: 2.0,
    }
    rows = [_row(day, "Cloud Run", "Requests", 2.0)]

    report = build_report(rows, daily, day, anomaly_factor=2.0)

    assert report.ratio == 2.0
    assert report.is_anomaly is False


def test_a_day_strictly_above_the_factor_flags():
    day = date(2026, 8, 5)
    daily = {
        date(2026, 8, 1): 1.0,
        date(2026, 8, 2): 1.0,
        date(2026, 8, 3): 1.0,
        date(2026, 8, 4): 1.0,
        day: 2.5,
    }
    rows = [_row(day, "Cloud Run", "Requests", 2.5)]

    report = build_report(rows, daily, day, anomaly_factor=2.0)

    assert report.is_anomaly is True
    assert report.ratio == 2.5


def test_month_to_date_covers_the_month_the_report_date_belongs_to():
    """Run on the 1st, the report is for the last day of the previous month — and closes it out."""
    report_date = date(2026, 7, 31)
    rows = [
        _row(date(2026, 7, 1), "Cloud Run", "Requests", 1.0),
        _row(date(2026, 7, 15), "Cloud Run", "Requests", 2.0),
        _row(report_date, "Cloud Run", "Requests", 3.0),
        _row(date(2026, 8, 1), "Cloud Run", "Requests", 99.0),
    ]

    report = build_report(rows, {}, report_date, anomaly_factor=2.0)

    assert report.mtd_gross == 6.0
    assert report.day_gross == 3.0


def test_a_day_with_no_rows_reports_zero_without_crashing():
    """The entry point raises on this; the domain layer must not be the thing that explodes."""
    report = build_report([], {}, date(2026, 8, 5), anomaly_factor=2.0)

    assert report.services == []
    assert report.vertex_models == []
    assert report.day_gross == 0.0
    assert report.is_anomaly is False
