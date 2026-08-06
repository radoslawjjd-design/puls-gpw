# Daily Cost Report — Plan Brief

> Full plan: `context/changes/daily-cost-report/plan.md`
> Research: `context/changes/daily-cost-report/research.md`

## What & Why

A fifth Cloud Run Job mails the owner a per-service GCP cost summary every morning at 09:00
Europe/Warsaw, flagging in the subject when the day's gross departs from the trailing 7-day median.
PUL-69 established the cost of not having this: a suspected period of high Gemini spend could
neither be confirmed nor ruled out, because the billing export only began 2026-07-16 and nothing
was watching. A spike is cheap to catch on the day and expensive to reconstruct months later.

## Starting Point

Cost is invisible until a human opens the console and writes a query. The billing export table
exists, is healthy, and was verified live during research — same project and dataset as the app
tables, and the runner service account can already read it. Four Cloud Run Jobs share one image and
one wiring pattern, so the fifth is mechanically a paste. What does not exist anywhere in the repo:
anomaly detection, a rolling median, a query reading a REPEATED RECORD column, an HTML table in an
email, or any cost-related code at all.

## Desired End State

Each morning an email arrives with yesterday's cost per service (gross and net), Vertex AI split by
model with token counts, and a month-to-date total. Yesterday is explicitly marked provisional.
When gross exceeds the trailing 7-day median by the configured factor, the subject says so and
names the ratio. A query or send failure emails an alert and exits non-zero rather than dying
quietly.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Reporting window | D-1 provisional + month-to-date | Billing rows keep arriving for 1-2 days, so a bare D-1 reads low and never corrects itself | Ticket |
| Cadence | Daily, anomaly in the subject | Threshold-only sending makes silence ambiguous between "nothing unusual" and "the job died" | Ticket |
| Anomaly baseline | Gross, never net | Net is −0.0001 PLN every day because a trial credit offsets everything, so a rule on net could never fire | Research |
| Anomaly factor | 2.0 via `COST_ANOMALY_FACTOR` | Fires twice in 21 days including the 08-05 spike this ticket exists to catch; 2.5 would have fired never | Plan |
| Median computation | Python, not SQL | `PERCENTILE_CONT` rejects a window frame in BigQuery, and pure Python unit-tests without BQ | Research |
| Anomaly scope | Day total only | Matches the acceptance criteria; the per-service table is there for a human to read | Plan |
| Insufficient baseline | Under 4 days → no flag | A median over one or two days is random and would fire falsely exactly when trust is being built | Plan |
| Month-to-date on the 1st | Month of D-1 | Keeps both figures on the same day and closes each month out exactly once | Plan |
| Zero rows | Raise and alert | Zero rows means the query broke, not that the day was free; a 0,00 zł mail looks like a healthy morning | Plan |
| Mail format | HTML in the Faro chrome | The builder is a copy-paste of `_announcement_digest_html`, and a cost table reads badly as plain text | Plan |
| `infra.md` | Fix both missing jobs | `puls-gpw-etf-quotes` was never recorded either; fixing it now stops the doc drifting further | Plan |

## Scope

**In scope:** two BigQuery read functions; a pure-Python report module (SKU→model mapping, median,
threshold, month-to-date); an HTML mail builder and sender; the `cost_report_main.py` entry point;
the `deploy.yml` step; `.env.example`; `infra.md` registries plus a provisioning runbook; a
read-only round-trip script.

**Out of scope:** credit remaining or expiry (not in the export); per-service anomaly flags; job
and scheduler creation from CI (human-only); any UI, API endpoint or new BigQuery table;
backfilling historical reports.

## Architecture / Approach

Aggregation in SQL, decisions in Python. The query stays a plain `GROUP BY` whose shape is asserted
as a string; the median, threshold, model mapping and sufficiency rule live in pure functions that
test without BigQuery. This follows directly from the project's own lesson that mocked BQ tests do
not validate SQL — so the less logic hides in SQL, the more of it is actually covered.

```
Cloud Scheduler 09:00 Warsaw
    → Cloud Run Job puls-gpw-cost-report (cost_report_main.py)
        → db.bigquery: get_billing_rows / get_daily_gross
        → src.cost_report: map SKUs, median, threshold  → CostReport
        → src.notifier: send_cost_report_email
    → owner mailbox   (failures → send_alert → exit 1)
```

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. BigQuery read layer | Two read functions + read-only round-trip script | First `UNNEST` over a table column in this repo; mocked tests cannot validate the syntax |
| 2. Report logic | `src/cost_report.py` — mapping, median, threshold, MTD | "Flash Lite" contains "Flash": wrong branch order silently collapses the breakdown |
| 3. Mail rendering | `_cost_report_html` + `send_cost_report_email` | First HTML table in a mail here; must escape every value and not overflow in Gmail |
| 4. Entry point | `cost_report_main.py` + ruff E402 entry | The zero-row guard must raise inside the `try`, or the alert carries no traceback |
| 5. Deployment wiring | `deploy.yml` step, `.env.example`, `infra.md` + runbook | The job must exist in GCP **before** merge — CI only runs `jobs update`, never `create` |

**Prerequisites:** none in code. One operational prerequisite blocks the merge, not the work: a
human must run `gcloud run jobs create` and `gcloud scheduler jobs create` before the PR lands.
**Estimated effort:** ~1-2 sessions across 5 phases; phases 1-4 suit `/10x-tdd`, phase 5 is
configuration and docs.

## Open Risks & Assumptions

- **The anomaly factor is a guess informed by 21 days of data.** 2.0 fires roughly one morning in
  ten on current volumes. It is an env var precisely so it can be raised without a deploy.
- **D-1 is 85-100 % complete at 09:00**, so the check can miss a same-day spike. The direction is
  safe (false negatives, not false alarms) and the next morning's month-to-date covers the miss.
- **The report is only as alive as `send_alert`.** Jobs run with `--max-retries=0` and the Cloud
  Monitoring policy still has no notification channel, so a crash before the alert fires is silent.
- **The table is partitioned on ingestion time**, so the date predicate prunes nothing and every
  run scans the whole table. Irrelevant at this size; it would not be at scale.

## Success Criteria (Summary)

- A mail arrives every morning at 09:00 Warsaw with yesterday's per-service cost, gross and net,
  the Vertex breakdown by model, and the month-to-date total — with yesterday marked provisional.
- On a day like 2026-08-05 the subject says the cost is anomalous and by how much.
- A broken query produces an alert email and a red execution, never a quiet 0,00 zł report.
