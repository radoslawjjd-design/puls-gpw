"""Everything the daily cost report decides, as pure functions (PUL-125).

No BigQuery, no SMTP, no clock — the report date is injected and the anomaly
factor is a parameter. `db/bigquery.py` fetches, `cost_report_main.py` reads the
environment, `src/notifier.py` renders. This module only reasons.

Two things here are not obvious.

**The anomaly baseline is gross, not net.** Measured across the whole export,
net is −0.0001 PLN on every single day: a `FreeTrialUpgrade` promotional credit
cancels the entire bill. A rule on net could never fire. Both figures are
reported so the mail stays honest about what is actually being paid, but only
gross is watched.

**The median is computed here rather than in SQL** because BigQuery rejects a
window frame on `PERCENTILE_CONT` ("Window ORDER BY is not allowed for analytic
function percentile_cont"). That turned out to be the better place for it
anyway — `context/foundation/lessons.md` records that mocked BigQuery tests do
not validate SQL, so a decision expressed in SQL is a decision that only the
manual round-trip really tests.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

# The public app URL the mail's logo resolves against. `main.py` has the same
# literal, but privately and behind a module that runs load_dotenv() and a large
# db.bigquery import block at import time — so a second entry point cannot
# import it. Overridden by APP_BASE_URL where it is set.
DEFAULT_BASE_URL = "https://puls-gpw-api-5zlombicra-lm.a.run.app"

# Days of history the median needs before it is allowed to judge anything. The
# export is young and the job may run on a partly-filled window; a median over
# two days is a coin flip that would either cry wolf or hide a real spike.
_MIN_BASELINE_DAYS = 4

# How far back the baseline reaches. Seven days so a weekly rhythm (the scraper
# runs on business days) sits inside the window rather than skewing it.
#
# Public because the entry point has to fetch exactly this many days. It used to
# hold its own copy, which is a silent-failure shape: shrink the fetch and the
# median quietly runs on fewer days than _MIN_BASELINE_DAYS wants, so
# trailing_median returns None forever, the flag never fires again, and every
# test stays green because each side is self-consistent.
BASELINE_WINDOW_DAYS = 7

_VERTEX_SERVICE = "Vertex AI"

# Model keys. Values are the human-facing names the mail prints.
_FLASH = "gemini-2.5-flash"
_FLASH_LITE = "gemini-2.5-flash-lite"

# Vertex SKUs that classify_sku could not place. They still carry money, so they
# get a row of their own — a model table that does not sum to its service line
# contradicts itself, and the reader has no way to tell which number is wrong.
_OTHER_MODEL = "other"


@dataclass
class CostReport:
    """What the mail renders. Lists are dicts, not nested dataclasses, because
    `src/notifier.py` takes primitives — it imports zero `src.*` and zero `db.*`
    today and keeping it that way means the caller unpacks, not the mailer."""

    report_date: date
    services: list[dict]
    vertex_models: list[dict]
    day_gross: float
    day_net: float
    mtd_gross: float
    mtd_net: float
    median_7d: float | None
    ratio: float | None
    is_anomaly: bool
    baseline_days: int


def classify_sku(sku_description: str) -> tuple[str, str] | None:
    """Map a billing SKU to (model, "input" | "output"), or None if it is not a Gemini line.

    Every branch here is defending against a string-matching trap found in the
    live export, so the order and the choice of substrings are load-bearing:

    * Two SKUs start with a literal space (` Gemini 2.5 Flash GA Input Text
      Caching`), so the description is stripped before anything else looks at it.
    * `"Gemini 2.5 Flash Lite …"` contains `"Flash"`. The Lite test runs first or
      both models collapse into one row and the Lite spend disappears into Flash.
    * Every prediction SKU ends in `- Predictions`, so direction is decided by a
      substring, never by a suffix. `endswith("Output")` matches none of them.
    * Caching is an input-side SKU and is attributed to its own model — the
      export carries a separate caching SKU per model, so nothing is shared.
    """
    desc = (sku_description or "").strip()
    if "Gemini" not in desc:
        return None

    # Matched on the full model name, not on "Lite" / "GA" alone. A bare "GA"
    # test would file a future "Gemini 3 Pro GA Text Input" under Flash: the
    # per-model sum would still reconcile against the Vertex service line and
    # every test would pass, while the table quietly attributed another model's
    # spend to this one. All seven live SKUs carry the full name, so this costs
    # nothing and an unrecognised model correctly falls through to "other".
    if "Flash Lite" in desc:
        model = _FLASH_LITE
    elif "Flash GA" in desc:
        model = _FLASH
    else:
        return None

    if "Output" in desc:
        return model, "output"
    if "Input" in desc:
        return model, "input"
    return None


def trailing_median(daily: dict[date, float], as_of: date) -> float | None:
    """Median gross over the up-to-7 days *before* as_of, or None if too few days.

    `as_of` is excluded from its own baseline. Including it would let a spike
    raise the very bar it is being judged against — at seven points one outlier
    barely moves a median, but on a three-day window it moves it a lot, and
    those are exactly the windows this job will see in its first week.
    """
    window_start = as_of - timedelta(days=BASELINE_WINDOW_DAYS)
    values = [v for day, v in daily.items() if window_start <= day < as_of]
    if len(values) < _MIN_BASELINE_DAYS:
        return None
    return median(values)


def _baseline_day_count(daily: dict[date, float], as_of: date) -> int:
    window_start = as_of - timedelta(days=BASELINE_WINDOW_DAYS)
    return sum(1 for day in daily if window_start <= day < as_of)


def _sum_services(rows: list[dict]) -> list[dict]:
    totals: dict[str, dict] = {}
    for r in rows:
        name = r.get("service") or "(unknown)"
        line = totals.setdefault(name, {"name": name, "gross": 0.0, "net": 0.0})
        line["gross"] += r.get("gross") or 0.0
        line["net"] += r.get("net") or 0.0
    return sorted(totals.values(), key=lambda line: line["gross"], reverse=True)


def _sum_vertex_models(rows: list[dict]) -> list[dict]:
    totals: dict[str, dict] = {}
    for r in rows:
        if r.get("service") != _VERTEX_SERVICE:
            continue
        classified = classify_sku(r.get("sku") or "")
        model, direction = classified if classified else (_OTHER_MODEL, None)
        line = totals.setdefault(
            model, {"model": model, "gross": 0.0, "input_tokens": 0.0, "output_tokens": 0.0}
        )
        line["gross"] += r.get("gross") or 0.0
        # An unplaced SKU keeps its money but not its tokens: without a direction
        # there is no honest column to put them in, and inventing one is how a
        # table starts lying quietly.
        if direction:
            line[f"{direction}_tokens"] += r.get("usage_amount") or 0.0
    return sorted(totals.values(), key=lambda line: line["gross"], reverse=True)


def build_report(
    rows: list[dict],
    daily_gross: dict[date, float],
    report_date: date,
    anomaly_factor: float,
) -> CostReport:
    """Assemble the report for `report_date` from month-to-date rows and a daily-gross baseline.

    `rows` must span the whole month `report_date` falls in, up to and including
    that date — the month-to-date figure is read straight off them. `daily_gross`
    must reach at least 7 days back, which is why it is a separate query: early
    in a month the baseline window starts in the previous month while the
    month-to-date window cannot.

    Note the month is the one `report_date` belongs to, not the calendar month
    the job runs in. Run on the 1st, the report closes out the month that just
    ended rather than showing a near-empty new one.
    """
    day_rows = [r for r in rows if r.get("day") == report_date]
    month_start = report_date.replace(day=1)
    mtd_rows = [r for r in rows if r.get("day") and month_start <= r["day"] <= report_date]

    day_gross = sum(r.get("gross") or 0.0 for r in day_rows)
    day_net = sum(r.get("net") or 0.0 for r in day_rows)

    median_7d = trailing_median(daily_gross, report_date)
    # A zero median makes the ratio meaningless (and undefined). Treat it the
    # same as no baseline: report the number, judge nothing.
    ratio = day_gross / median_7d if median_7d else None

    return CostReport(
        report_date=report_date,
        services=_sum_services(day_rows),
        vertex_models=_sum_vertex_models(day_rows),
        day_gross=day_gross,
        day_net=day_net,
        mtd_gross=sum(r.get("gross") or 0.0 for r in mtd_rows),
        mtd_net=sum(r.get("net") or 0.0 for r in mtd_rows),
        median_7d=median_7d,
        ratio=ratio,
        is_anomaly=ratio is not None and ratio > anomaly_factor,
        baseline_days=_baseline_day_count(daily_gross, report_date),
    )
