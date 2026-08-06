# Daily Cost Report Implementation Plan

## Overview

Add a fifth Cloud Run Job, `puls-gpw-cost-report`, that runs daily at 09:00 Europe/Warsaw, reads
the GCP billing export from BigQuery, and emails the owner a per-service cost summary for the
previous day plus a month-to-date total. When the day's gross cost exceeds the trailing 7-day
median by a configured factor, the subject line says so.

The point is not the mail. It is that a spike becomes visible on the day it happens. PUL-69
established that a suspected period of high Gemini spend could not be confirmed or ruled out
after the fact, because nothing was watching.

## Current State Analysis

Cost is invisible until a human opens the console and writes a query. The billing export table
exists and is healthy — verified live during research, not assumed:

- `puls-gpw.espi_ebi.gcp_billing_export_v1_01E214_63C8A3_9E57E3`, `bq show` exit 0
- Same project and dataset as the app tables, so `_table_ref` (`db/bigquery.py:116-117`) composes
  it with no new helper
- Ingestion-time day-partitioned (`"timePartitioning": {"type": "DAY"}`, no `field`), and no
  `requirePartitionFilter` — so a predicate on `usage_start_time` is legal but prunes nothing
- `puls-gpw-runner@` already holds `roles/bigquery.dataEditor` and `roles/bigquery.jobUser`, so
  **no IAM change and no new secret** are needed

Four existing Cloud Run Jobs share one image and one wiring pattern. The fifth is mechanically a
paste, with one wrinkle: CI only ever runs `gcloud run jobs update`, never `create`
(`.github/workflows/deploy.yml:60-92`), so the job must exist in GCP before the merge.

What does not exist anywhere in the repo: anomaly detection, a rolling median, a query that reads
a REPEATED RECORD column from a table, an HTML table in an email, and any cost-related code at all.
PUL-69's queries were written as markdown and never committed as runnable code.

### Key Discoveries:

- **Net is structurally zero.** Measured across the whole export, net is `−0.0001 PLN` every day —
  a `FreeTrialUpgrade` promotional credit offsets all gross. An anomaly rule on net could never
  fire. Both figures are reported; only gross is watched.
- **`PERCENTILE_CONT` cannot take a window frame in BigQuery.** `OVER (ORDER BY day ROWS BETWEEN 7
  PRECEDING AND 1 PRECEDING)` is rejected with *"Window ORDER BY is not allowed for analytic
  function percentile_cont"*. The median is therefore computed in Python.
- **D-1 at 09:00 holds 85–100 % of its final cost** (median ~99 %, worst observed 85.2 %,
  measured by comparing `export_time` against the 09:00 Warsaw cutoff). "Provisional" is warranted,
  and the understatement biases the check toward false negatives — the safe direction.
- **`"Gemini 2.5 Flash Lite"` contains the substring `"Flash"`.** A `LIKE '%Flash%'` branch tested
  before the Lite branch silently collapses both models into one row.
- **`usage.unit` reads `requests` but carries token counts** (1,065,040 for one day of Flash GA
  input). Google's naming, not ours — the mail must label it *tokens*.
- **The emptiness guard has a precedent to copy**: `etf_quotes_main.py:42-45` raises rather than
  writing an empty result.
- **`sys.exit(1)` reaches nobody.** Jobs run with `--max-retries=0` and the Cloud Monitoring alert
  policy has no notification channel (`context/deployment/deploy-plan.md:66`). `send_alert` is the
  entire path to a human.

## Desired End State

Every morning at 09:00 Europe/Warsaw an email arrives titled with the previous day's gross cost,
carrying a per-service table (gross and net), a Vertex AI breakdown by model with token counts, and
a month-to-date total. Yesterday's figure is explicitly marked provisional. When gross exceeds the
trailing 7-day median by `COST_ANOMALY_FACTOR`, the subject says so. A query or send failure emails
an alert and exits non-zero instead of failing silently.

**Verification**: the 09:00 mail arrives for three consecutive days; a manual run with the factor
temporarily lowered produces the anomaly subject; a manual run against a date with no billing rows
raises and alerts rather than sending a 0,00 zł report.

## What We're NOT Doing

- **Not showing credit remaining or its expiry** — not in the billing export (PUL-69). The report
  shows credit *consumed* only.
- **Not flagging anomalies per service.** The subject watches the day total only, per the ticket's
  acceptance criteria. The per-service table is there for a human to read.
- **Not creating the Cloud Run Job or the Cloud Scheduler entry from CI.** Human-only, once, before
  the merge.
- **Not adding a UI, an API endpoint, or a BigQuery table.** Nothing consumes this but a mailbox.
- **Not backfilling historical reports.** The report starts the morning after it ships.
- **Not adding a `_SCHEMA`, `create_*_if_not_exists()` or `ensure_*_schema_current()` for the
  billing table.** Google owns that table; our DDL must never touch it.
- **Not adding `cost_report` to `tach.toml`** — precedent is that `company_stats_main` and
  `etf_quotes_main` were never added either.

## Implementation Approach

Aggregation in SQL, decisions in Python. The project has been bitten twice by SQL that passed
mocked tests and failed on real BigQuery (reserved keywords, PUL-29; a `REQUIRED` column, PUL-88),
and `context/foundation/lessons.md:211-235` records that mocked BQ tests do not validate SQL. So
the query stays a plain `GROUP BY` whose shape is asserted as a string, and every interesting
decision — the median, the threshold, the model mapping, the sufficiency rule — lives in pure
Python functions that unit-test without BigQuery.

Phase order mirrors the newest job-adding change (`context/archive/2026-06-29-pul-67/plan.md`):
data layer → domain logic → presentation → entry point → CI/CD last. The final phase is modelled
on the company-stats change's dedicated deployment-wiring phase
(`context/archive/2026-06-25-daily-company-stats-snapshot-ingestion/plan.md:299-360`), which
updated `infra.md` — the step PUL-67 skipped, which is why that doc is stale today.

## Critical Implementation Details

**Ordering: the job must exist in GCP before this branch merges.** CI runs `gcloud run jobs update`
only. Merging before the human provisioning step turns the deploy step red and blocks the pipeline
for every other change. The runbook lands in Phase 5 but must be *executed* before the PR merges.

**The Vertex model mapping is order-dependent and fails quietly.** `"Gemini 2.5 Flash Lite Text
Input - Predictions"` matches a `Flash` test as readily as `"Gemini 2.5 Flash GA Text Input"`. The
Lite branch must be evaluated first. Wrong ordering produces a plausible-looking single-model
breakdown that no reviewer would question — hence a dedicated test naming both SKUs.

**`send_alert` reads the ambient traceback, not its argument.** `traceback.format_exc()` called
outside an `except` block prints `NoneType: None` (`main.py:188` already does this). The zero-row
guard must raise from inside the `try`, so the alert carries a real traceback.

## Phase 1: BigQuery read layer

### Overview

One read function returning the raw material for the report, and one manual round-trip that proves
the SQL runs against the real table.

### Changes Required:

#### 1. Billing export table constant and read function

**File**: `db/bigquery.py`

**Intent**: Add the billing export table name alongside the other table-name constants, and a read
function that returns per-SKU cost rows for a date range plus daily gross totals. The function does
aggregation only — no medians, no thresholds, no model mapping.

**Contract**: `_GCP_BILLING_EXPORT_TABLE_NAME = "gcp_billing_export_v1_01E214_63C8A3_9E57E3"`, used
through the existing `_table_ref(client, ...)`. Two public functions:

- `get_billing_rows(start: date, end: date) -> list[dict]` — one row per
  (day, service, sku), with `day`, `service`, `sku`, `gross`, `net`, `usage_amount`, `usage_unit`.
  Days bucketed as `DATE(usage_start_time, 'Europe/Warsaw')`; `net` is gross plus the summed
  `credits.amount`.
- `get_daily_gross(start: date, end: date) -> dict[date, float]` — one gross total per day, for the
  median baseline.

Both bind `start`/`end` as `ScalarQueryParameter(..., "DATE", ...)`, follow the read template at
`db/bigquery.py:3895-3926`, and re-raise as `BigQueryError(f"<fn> failed: {exc}") from exc`.

The credit join is the repo's first `UNNEST` over a table column rather than a parameter — say so
in the docstring, as this codebase does. The correlated-subquery form is known to work here:

```sql
SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS net
```

#### 2. Manual round-trip against the live table

**File**: `scripts/test_bq_billing_export.py`

**Intent**: Genre-B smoke script in the style of `scripts/test_bq_etf_quotes.py` — run both new
read functions against the real export for the last 8 days and print the shapes, so a syntax error
surfaces before 09:00 rather than as an alert.

**Contract**: Standalone `main()` with the house preamble (`sys.path.insert`, `load_dotenv()`), a
`# Run with: uv run python scripts/test_bq_billing_export.py` docstring, and **read-only** — unlike
the other round-trips it cannot use a throwaway table, because Google owns this one. No writes, no
DDL, no `--apply` flag.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_bigquery_cost_report.py`
- The built SQL buckets days in Warsaw: the query string contains `'Europe/Warsaw'`
- The built SQL joins credits: the query string contains `UNNEST(credits)`
- Date bounds are bound as parameters, not interpolated: params include `start`/`end` with
  `type_ == "DATE"`
- Full suite passes: `uv run pytest`
- Linting passes: `uv run ruff check .`

#### Manual Verification:

- `uv run python scripts/test_bq_billing_export.py` exits 0 and prints non-empty rows for the last
  8 days
- Printed daily gross for a settled day matches a hand-run `bq query` for the same day

---

## Phase 2: Report logic

### Overview

Every decision the report makes, as pure functions over the rows Phase 1 returns. No BigQuery, no
SMTP, no clock beyond an injected date.

### Changes Required:

#### 1. Cost report domain module

**File**: `src/cost_report.py`

**Intent**: Turn raw billing rows into the exact structure the mail renders, and decide whether the
day is anomalous.

**Contract**: A `CostReport` dataclass carrying `report_date`, `services` (name, gross, net),
`vertex_models` (model, gross, input_tokens, output_tokens), `day_gross`, `day_net`, `mtd_gross`,
`mtd_net`, `median_7d`, `ratio`, `is_anomaly`, `baseline_days`. Plus:

- `map_sku_to_model(sku_description: str) -> str | None` — **the Lite branch is tested before the
  Flash branch**; returns `None` for non-Gemini SKUs.
- `trailing_median(daily: dict[date, float], as_of: date) -> float | None` — median over the up-to-7
  days before `as_of`, returning `None` when fewer than `_MIN_BASELINE_DAYS` (4) are present.
- `build_report(rows, daily_gross, report_date) -> CostReport` — assembles the above; the
  month-to-date window is **the month `report_date` belongs to**, so on the 1st of a month the
  report closes out the previous month rather than showing a near-zero current one.
- `_ANOMALY_FACTOR = float(os.environ.get("COST_ANOMALY_FACTOR", "2.0"))`, following the house
  env-with-literal-default pattern (`src/http_client.py:12-14`, `src/parser.py:21-23`).

`is_anomaly` is `False` whenever `median_7d` is `None` — an insufficient baseline never flags.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_cost_report.py`
- Both Gemini SKUs map to distinct models: a test naming `"Gemini 2.5 Flash Lite Text Input -
  Predictions"` and `"Gemini 2.5 Flash GA Text Input - Predictions"` asserts two different values
- Median over fewer than 4 days returns `None` and suppresses the flag
- A day at exactly the factor does not flag; strictly above it does
- Month-to-date on the 1st of a month covers the previous month
- Full suite passes: `uv run pytest`

#### Manual Verification:

- Feeding the real 2026-08-05 rows produces `ratio` ≈ 2.17 and `is_anomaly = True` at factor 2.0

---

## Phase 3: Mail rendering and sender

### Overview

The HTML report and its public sender, following the Faro chrome and the escaping rule the repo
already enforces.

### Changes Required:

#### 1. Cost report HTML builder and sender

**File**: `src/notifier.py`

**Intent**: Render a `CostReport` as an HTML mail in the existing Faro chrome and send it to the
owner, matching how every other sender wraps `_send`.

**Contract**: Two additions, modelled on `_announcement_digest_html` (`:303-348`) and
`send_announcement_digest_email` (`:351-365`):

- `_cost_report_html(report: CostReport, base_url: str) -> str` — navy `#14304A` header with the
  Faro mark from `{base_url}/static/img/faro-mark.png`, a per-service table (gross and net), a
  Vertex-by-model table whose token column is labelled **tokens**, the month-to-date line, a note
  that the previous day is provisional because billing rows keep arriving for 1-2 days, and a note
  that credit remaining is not available from the export. Every interpolated value goes through
  `html.escape(..., quote=True)`.
- `send_cost_report_email(report: CostReport, base_url: str) -> None` — calls `_send(subject, html,
  html=True)` with no `to` and no `from_name`, matching the owner-mail convention. Subject is
  prefixed `[puls-gpw]`, carries the date and gross, and states the anomaly when `is_anomaly` —
  including the ratio, so the subject alone answers "how much worse". Raises on failure; the
  docstring says so, as `:275-277` does.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_notifier.py`
- The sender is wired correctly: patching `src.notifier._send` shows `html=True`, no `to`, no
  `from_name`
- The anomaly subject differs from the normal subject and names the ratio
- A hostile service name is escaped: the raw payload is absent from the HTML and its escaped form
  present
- The token column is labelled tokens, not requests
- Full suite passes: `uv run pytest`

#### Manual Verification:

- The rendered HTML opens correctly in Gmail on desktop and mobile — tables do not overflow

---

## Phase 4: Cloud Run Job entry point

### Overview

The script the job runs: resolve yesterday, query, build, send, and fail loudly.

### Changes Required:

#### 1. Entry point

**File**: `cost_report_main.py`

**Intent**: Wire Phases 1-3 together under the entry-point contract every other job follows.

**Contract**: Import order is load-bearing — `load_dotenv()` → `configure_logging()` → then `db.*`
and `src.*` (`context/foundation/lessons.md:5-21`), with `WARSAW = ZoneInfo("Europe/Warsaw")` and
`report_date = datetime.now(WARSAW).date() - timedelta(days=1)`. The body queries the 8-day window
plus the month-to-date window, builds the report, and sends it.

**The zero-row guard raises from inside the `try`**, mirroring `etf_quotes_main.py:42-45`: no
billing rows for the report date means the query broke, not that the day was free. The failure
block is the four-job invariant verbatim — `logger.exception` → nested `try: send_alert(exc)` →
`logger.error` on alert failure → `sys.exit(1)`.

#### 2. Ruff exemption

**File**: `pyproject.toml`

**Intent**: Register the new entry point in the E402 per-file-ignores, or lint fails on the
mandatory `load_dotenv()`-before-imports ordering.

**Contract**: `"cost_report_main.py" = ["E402"]` in `[tool.ruff.lint.per-file-ignores]`.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_cost_report_main.py`
- The happy path sends exactly one mail and does not alert
- A query failure alerts and exits non-zero
- Zero billing rows alerts and exits non-zero, and **no mail is sent**
- An alert that itself fails is logged and still exits non-zero
- Linting passes: `uv run ruff check .` (proves the E402 entry is present)
- Full suite passes: `uv run pytest`

#### Manual Verification:

- `uv run python cost_report_main.py` locally against real credentials delivers a correct mail

---

## Phase 5: Deployment wiring and documentation

### Overview

CI updates the job; a human creates it. Both, plus the docs that have already drifted a job behind.

### Changes Required:

#### 1. Deploy step

**File**: `.github/workflows/deploy.yml`

**Intent**: Add a fifth `gcloud run jobs update` step so every push to master ships the new job's
image, and pass the job's env explicitly rather than relying on values frozen at create time.

**Contract**: A step after line 92 following the etf-quotes shape, with
`--args="run,--no-dev,python,cost_report_main.py"` and — unlike the other non-post jobs —
`--update-env-vars="COST_ANOMALY_FACTOR=2.0,GOOGLE_CLOUD_PROJECT=…,BIGQUERY_DATASET=espi_ebi"`.
Additive `--update-*` semantics, so the SMTP secrets set at create time survive. No change to
`paths-ignore`: a root-level `.py` matches neither entry, and
`tests/test_deploy_workflow_filter.py` keeps passing.

#### 2. Local env documentation

**File**: `.env.example`

**Intent**: Document the first cost-related knob so a local run is reproducible.

**Contract**: A `# Cost report (PUL-125)` block with `COST_ANOMALY_FACTOR=2.0` and a one-line note
that it multiplies the trailing 7-day median.

#### 3. Infrastructure documentation

**File**: `context/foundation/infra.md`

**Intent**: Bring the two registries back in line with reality and add the provisioning runbook.
The doc is stale by one job already — `puls-gpw-etf-quotes` and its trigger were never recorded and
the prose still says "wszystkie trzy joby".

**Contract**: Rows for **both** `puls-gpw-etf-quotes` and `puls-gpw-cost-report` in the Cloud Run
Jobs table (`:9-13`) and the Cloud Scheduler table (`:90-96`); the "trzy joby" prose corrected; and
a HUMAN-ONLY runbook appended in the shape of `:100-132` — `gcloud run jobs create
puls-gpw-cost-report` with the five SMTP secrets, `COST_ANOMALY_FACTOR`, cpu 1 / memory 1Gi /
task-timeout 300s, then `gcloud scheduler jobs create http puls-gpw-cost-report-trigger` with
`--schedule="0 9 * * *"` and `--time-zone="Europe/Warsaw"`, then the two `list` verification
commands.

### Success Criteria:

#### Automated Verification:

- The deploy filter invariant still holds: `uv run pytest tests/test_deploy_workflow_filter.py`
- The workflow is valid YAML and the new step names the new script: `uv run pytest`
- Linting passes: `uv run ruff check .`

#### Manual Verification:

- The runbook's `jobs create` and `scheduler jobs create` commands have been run and both `list`
  commands show the new resources — **executed before the PR merges**, since CI only updates
- After merge, the deploy workflow's cost-report step is green
- A manual `gcloud run jobs execute puls-gpw-cost-report` delivers the mail
- The next morning's 09:00 mail arrives unprompted

---

## Testing Strategy

### Unit Tests:

- **SQL shape** (`tests/test_bigquery_cost_report.py`): patch `db.bigquery._get_client`, assert the
  built query contains `'Europe/Warsaw'` and `UNNEST(credits)`, and that bounds are `DATE`
  parameters. Mocked BQ does not validate syntax — that is what the round-trip script is for.
- **Report logic** (`tests/test_cost_report.py`): the Lite-before-Flash mapping with both real SKU
  strings; median with 3 days (suppressed) and 7 days; ratio exactly at the factor vs above it;
  month-to-date on the 1st of a month; a report whose services list is empty.
- **Mail** (`tests/test_notifier.py`): sender wiring via `patch("src.notifier._send")`; escaping of
  a hostile service name; anomaly vs normal subject.
- **Entry point** (`tests/test_cost_report_main.py`): patch collaborators on the *importing* module
  (`monkeypatch.setattr(cost_report_main, "send_alert", ...)`), plus `sys.exit`, following
  `tests/test_company_stats_main.py:89-112`. Cover happy path, query failure, zero rows, and a
  failing alert.

### Integration Tests:

None. Nothing consumes this but a mailbox, and the only real integration — BigQuery syntax — is
covered by the manual round-trip, which is the project's established substitute.

### Manual Testing Steps:

1. `uv run python scripts/test_bq_billing_export.py` — rows print for the last 8 days
2. `uv run python cost_report_main.py` — a correct mail arrives
3. Temporarily set `COST_ANOMALY_FACTOR=0.5` and rerun — the subject carries the anomaly and ratio
4. Run for a date before the export began (2026-07-01) — it alerts and exits 1, and sends no report
5. After provisioning, `gcloud run jobs execute puls-gpw-cost-report` — the mail arrives from GCP
6. Confirm the unprompted 09:00 mail the following morning

## Performance Considerations

The export holds under 1,000 rows/day and roughly three weeks of history, so an 8-day scan is
trivial and the 300 s task timeout is far more than needed. Worth recording: the table is
partitioned on ingestion time, not `usage_start_time`, so the date predicate does **not** prune
partitions — every query scans the whole table. Harmless at this size; it would not be at ten
thousand times it.

## Migration Notes

No schema change and no data migration. The one ordering constraint is operational: the Cloud Run
Job and its scheduler must be created by hand **before** this branch merges, because CI only ever
runs `jobs update`. Rollback is deleting the scheduler entry — the job then never fires, and
nothing else depends on it.

## References

- Research: `context/changes/daily-cost-report/research.md`
- Decisions: `context/changes/daily-cost-report/change.md`
- Prior investigation: `context/archive/2026-08-06-vertex-ai-cost-verification/findings.md` (PUL-69)
- Job-adding precedent: `context/archive/2026-06-29-pul-67/plan.md`
- Deployment-wiring phase to mirror:
  `context/archive/2026-06-25-daily-company-stats-snapshot-ingestion/plan.md:299-360`
- Entry-point template: `etf_quotes_main.py:1-27, 42-45, 55-62`
- Read-query template: `db/bigquery.py:3895-3926`
- HTML mail template: `src/notifier.py:303-365`
- Provisioning runbook template: `context/foundation/infra.md:100-132`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: BigQuery read layer

#### Automated

- [ ] 1.1 Unit tests pass: `uv run pytest tests/test_bigquery_cost_report.py`
- [ ] 1.2 The built SQL buckets days in Warsaw
- [ ] 1.3 The built SQL joins credits via UNNEST
- [ ] 1.4 Date bounds are bound as DATE parameters
- [ ] 1.5 Full suite passes: `uv run pytest`
- [ ] 1.6 Linting passes: `uv run ruff check .`

#### Manual

- [ ] 1.7 Round-trip script exits 0 and prints non-empty rows for the last 8 days
- [ ] 1.8 Printed daily gross for a settled day matches a hand-run `bq query`

### Phase 2: Report logic

#### Automated

- [ ] 2.1 Unit tests pass: `uv run pytest tests/test_cost_report.py`
- [ ] 2.2 Both Gemini SKUs map to distinct models
- [ ] 2.3 Median over fewer than 4 days returns None and suppresses the flag
- [ ] 2.4 A day exactly at the factor does not flag; strictly above it does
- [ ] 2.5 Month-to-date on the 1st of a month covers the previous month
- [ ] 2.6 Full suite passes: `uv run pytest`

#### Manual

- [ ] 2.7 Real 2026-08-05 rows produce ratio ≈ 2.17 and is_anomaly True at factor 2.0

### Phase 3: Mail rendering and sender

#### Automated

- [ ] 3.1 Unit tests pass: `uv run pytest tests/test_notifier.py`
- [ ] 3.2 Sender wiring shows html=True, no to, no from_name
- [ ] 3.3 The anomaly subject differs from the normal subject and names the ratio
- [ ] 3.4 A hostile service name is escaped
- [ ] 3.5 The token column is labelled tokens, not requests
- [ ] 3.6 Full suite passes: `uv run pytest`

#### Manual

- [ ] 3.7 The rendered HTML opens correctly in Gmail on desktop and mobile

### Phase 4: Cloud Run Job entry point

#### Automated

- [ ] 4.1 Unit tests pass: `uv run pytest tests/test_cost_report_main.py`
- [ ] 4.2 The happy path sends exactly one mail and does not alert
- [ ] 4.3 A query failure alerts and exits non-zero
- [ ] 4.4 Zero billing rows alerts, exits non-zero, and sends no mail
- [ ] 4.5 A failing alert is logged and still exits non-zero
- [ ] 4.6 Linting passes: `uv run ruff check .`
- [ ] 4.7 Full suite passes: `uv run pytest`

#### Manual

- [ ] 4.8 A local run against real credentials delivers a correct mail

### Phase 5: Deployment wiring and documentation

#### Automated

- [ ] 5.1 Deploy filter invariant holds: `uv run pytest tests/test_deploy_workflow_filter.py`
- [ ] 5.2 The workflow is valid YAML and the new step names the new script
- [ ] 5.3 Linting passes: `uv run ruff check .`

#### Manual

- [ ] 5.4 Job and scheduler created by hand; both `list` commands show them — before the PR merges
- [ ] 5.5 After merge, the deploy workflow's cost-report step is green
- [ ] 5.6 A manual `gcloud run jobs execute` delivers the mail
- [ ] 5.7 The next morning's 09:00 mail arrives unprompted
