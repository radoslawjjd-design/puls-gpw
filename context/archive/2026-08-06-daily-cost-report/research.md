---
date: 2026-08-06T18:43:28+02:00
researcher: Radek
git_commit: cac35c8b84eab68260a58c4658ec056af12c3efb
branch: feat/pul-125-daily-cost-report
repository: radoslawjjd-design/puls-gpw
topic: "Daily cost report emailed at 09:00 with anomaly flagging (PUL-125)"
tags: [research, codebase, cloud-run-jobs, bigquery, billing-export, notifier, deploy]
status: complete
last_updated: 2026-08-06
last_updated_by: Radek
---

# Research: Daily cost report emailed at 09:00 with anomaly flagging

**Date**: 2026-08-06 18:43 +02:00
**Researcher**: Radek
**Git Commit**: `cac35c8b84eab68260a58c4658ec056af12c3efb`
**Branch**: `feat/pul-125-daily-cost-report`
**Repository**: radoslawjjd-design/puls-gpw

## Research Question

What does a fifth Cloud Run Job (`puls-gpw-cost-report`) have to touch, and what do the
existing job / SMTP / BigQuery layers already dictate about how it must be built?

## Summary

The infra half of this ticket is nearly free — the fifth job is a paste of the fourth, and
every convention it needs already exists and is documented. The interesting risk is entirely
in the query and the anomaly rule, and four things found here change the plan:

1. **The anomaly rule must run on gross, not net.** Measured over the full export: net is
   `−0.0001 PLN` *every single day*, because the trial credit offsets everything to zero. An
   anomaly rule on net can never fire. The mail still reports both — the acceptance criteria
   ask for gross and net — but the subject-line signal is a function of gross alone.
2. **`PERCENTILE_CONT` cannot express a trailing window in BigQuery.** `PERCENTILE_CONT(x, 0.5)
   OVER (ORDER BY day ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING)` fails with *"Window ORDER BY
   is not allowed for analytic function percentile_cont"* (verified against the live table).
   The median has to come from a self-join with `APPROX_QUANTILES`, or — the recommendation
   below — from Python over 8 returned rows.
3. **"Provisional" is justified, and now quantified.** At 09:00 the next morning, D-1 holds
   **85–100 %** of what it will eventually hold (median ~99 %, worst observed 85.2 %). So the
   understatement is real but modest, and it biases the anomaly check toward false *negatives*.
4. **The report would have fired on the day PUL-69 flagged.** Simulating the rule over the
   21 days of export: ratio-to-trailing-median ranges 0.42–2.17, with 2026-08-05 at 2.17 and
   2026-07-22 at 2.03. A factor of 2.0 fires twice in three weeks; 2.5 fires never. This is the
   one number the plan has to pick deliberately.

There is no prior art for anomaly detection, rolling medians, or reading a REPEATED RECORD
column — this change writes the repo's first of each. Everything else is copy-the-pattern.

## Detailed Findings

### The billing export table — verified live, not from docs

`puls-gpw.espi_ebi.gcp_billing_export_v1_01E214_63C8A3_9E57E3`, `bq show` exit 0. Same project
and same dataset as the app tables, so the existing `_table_ref` helper composes it correctly.

Day-partitioned with `"timePartitioning": {"type": "DAY"}` and **no `field`** — i.e.
ingestion-time partitioned on `_PARTITIONTIME`, *not* on `usage_start_time`. There is **no**
`requirePartitionFilter`, so a query without a partition predicate is legal. Consequence worth
stating in the plan: filtering on `usage_start_time` does **not** prune partitions. At this
table's size (~21 days, <1000 rows/day) that costs nothing, but it is not a pattern to carry to
a large export.

Columns this change needs, from the live schema:

| Path | Type | Note |
|---|---|---|
| `cost` | FLOAT | gross, in PLN (`currency`) |
| `credits` | **REPEATED RECORD** | `.amount` (FLOAT, negative), `.name`, `.type` — needs `UNNEST` |
| `service.description` | STRING | `Vertex AI`, `Cloud Run`, … |
| `sku.description` | STRING | the model axis, see below |
| `usage.amount` / `usage.unit` | FLOAT / STRING | unit reads `requests`, but the values are **tokens** |
| `usage_start_time` | TIMESTAMP | UTC instant; day bucketing needs an explicit tz |
| `export_time` | TIMESTAMP | when the row landed — the basis for the completeness numbers below |

**`usage.unit` lies.** It reads `requests` while carrying 1,065,040 for a single day of Flash GA
input. These are token counts; the naming is Google's. Already recorded in
`context/archive/2026-08-06-vertex-ai-cost-verification/findings.md:85-87`. The mail must label
this column *tokens*, not *requests*, or it will read as an alarming call volume.

### Net is structurally zero — the finding that reshapes the anomaly rule

Measured per day across the whole export:

| Day | Gross PLN | Net PLN |
|---|---:|---:|
| 2026-08-05 | 2.778 | −0.0000 |
| 2026-08-04 | 1.278 | −0.0001 |
| 2026-08-03 | 1.040 | −0.0001 |
| 2026-08-02 | 0.881 | −0.0000 |
| 2026-08-01 | 0.652 | −0.0001 |
| 2026-07-31 | 1.976 | −0.0004 |

Every gross złoty is offset by `FreeTrialUpgrade:CreditId-FreeTrial:Credit-01E214-63C8A3-9E57E3`
(type `PROMOTION`). So:

- Report **both** figures (acceptance asks for it) — but net's job is to show *how much credit
  is being consumed*, not to be watched for spikes.
- Compute the anomaly on **gross**. A rule on net compares numbers that are all zero.
- The report cannot show credit *remaining* or its expiry — not in the export
  (`findings.md:69-72`). Say so in the mail body rather than leaving a reader to assume runway.

### Report-time completeness — how provisional is "provisional"

Computed by comparing each day's final gross against the subset whose `export_time` had already
landed by 09:00 Warsaw the following morning:

| Usage day | Final gross | Present at 09:00 D+1 | Complete |
|---|---:|---:|---:|
| 2026-08-05 | 2.778 | 2.746 | 98.8 % |
| 2026-08-04 | 1.278 | 1.138 | 89.1 % |
| 2026-08-03 | 1.040 | 0.886 | **85.2 %** |
| 2026-08-02 | 0.881 | 0.822 | 93.3 % |
| 2026-08-01 | 0.652 | 0.652 | 100 % |
| 2026-07-31 | 1.976 | 1.710 | 86.5 % |
| 2026-07-30 | 1.827 | 1.635 | 89.5 % |
| 2026-07-29 … 07-24 | — | — | 99.7–100 % |

So D-1 at 09:00 is understated by 0–15 %. Two consequences for the plan:

- The "provisional" label is warranted, and the MTD total is what makes it self-correcting —
  exactly the reasoning already recorded on the ticket.
- The anomaly check inherits the understatement. It can **miss** a spike on the day; it will not
  invent one. That asymmetry is the safe direction, and the next morning's MTD covers the miss.

A second-order effect observed directly: two identical queries run ~15 minutes apart returned
`0.2006` and `2.379` for the *current* day. In-flight days move a lot. The report must never
present the current day as a figure.

### The anomaly rule — what it would have done

Simulated with a self-join (`APPROX_QUANTILES(prior.gross, 2)[OFFSET(1)]` over days D−7…D−1):

| Day | Gross | Trailing median | Ratio |
|---|---:|---:|---:|
| 2026-08-05 | 2.778 | 1.278 | **2.17** |
| 2026-08-04 | 1.278 | 1.549 | 0.83 |
| 2026-07-31 | 1.976 | 1.549 | 1.28 |
| 2026-07-29 | 2.146 | 1.405 | 1.53 |
| 2026-07-22 | 2.912 | 1.434 | **2.03** |
| … full range | 0.652–2.912 | — | **0.42–2.17** |

Reading: normal variation already reaches 1.5×. A factor of **2.0 fires twice in 21 days**
(~10 % of mornings); **2.5 fires never** in this window. 2.0 catches the 08-05 Flash-GA spike
that PUL-69 could only reconstruct after the fact — which is precisely the event this ticket
exists to catch — at the cost of an occasional shrug. Recommendation: default **2.0**, exposed
as an env var so it can be raised without a deploy if it proves noisy.

**Blocking constraint on how the median is computed:**

```
Window ORDER BY is not allowed for analytic function percentile_cont
```

`PERCENTILE_CONT ... OVER (ORDER BY day ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING)` is rejected by
BigQuery. Two viable shapes remain:

1. Self-join + `APPROX_QUANTILES(p.gross, 2)[OFFSET(1)]` — verified working (produced the table
   above), but it is approximate and puts the rule inside SQL where mocked tests cannot check it.
2. **Return 8 daily totals and take the median in Python** (`statistics.median`) — exact, and it
   moves the rule into pure code that unit-tests without touching BigQuery. Given the project's
   own lesson that *mocked BQ tests do not validate SQL* (`context/foundation/lessons.md:211-235`),
   keeping the decision logic out of SQL is the more testable split. **Recommended.**

### Cloud Run Job wiring — five places, all mechanical

| # | Place | Reference | Needed |
|---|---|---|---|
| 1 | Entry point `cost_report_main.py` | template: `etf_quotes_main.py` | new file |
| 2 | Deploy step | `.github/workflows/deploy.yml:86-92` | paste a fifth block |
| 3 | Ruff E402 exemption | `pyproject.toml:49-60` | **must add** — lint fails without it |
| 4 | Job + scheduler creation | runbook `context/foundation/infra.md:100-132` | **human-only**, once, pre-merge |
| 5 | Infra docs | `context/foundation/infra.md:9-15, 90-96` | see the staleness note below |

Facts that constrain the plan:

- **CI only ever runs `gcloud run jobs update`, never `create`** (`deploy.yml:60-92`). The job
  must already exist in GCP before the first merge, or the deploy step fails. Same rule PUL-67
  recorded at `context/archive/2026-06-29-pul-67/plan.md:501`.
- Job steps use `--command=uv --args="run,--no-dev,python,<entry>.py"`, and secrets/env are
  applied **additively** (`--update-secrets` / `--update-env-vars`), unlike the API service which
  uses replace semantics (`deploy.yml:113-114`).
- Only the `post` job passes env in CI; `company-stats` and `etf-quotes` rely on values frozen at
  `create` time. For the new job, pass `GOOGLE_CLOUD_PROJECT`, `BIGQUERY_DATASET` and the anomaly
  factor **explicitly in the deploy step** — relying on the `"espi_ebi"` default at
  `db/bigquery.py:52` happens to be correct here but is an accident, not a guarantee.
- **No Dockerfile or `.dockerignore` change.** `COPY . .` (`Dockerfile:11`) already picks up any
  root-level `.py`, and `.dockerignore` excludes no Python.
- **The deploy path filter needs no change.** `paths-ignore` is a two-entry denylist
  (`context/**`, `**.md`, `deploy.yml:16-18`); a new root script matches neither.
  `tests/test_deploy_workflow_filter.py` keeps the filter and `.dockerignore` in agreement and
  will still pass.
- **IAM is already sufficient** — verified, not assumed. `puls-gpw-runner@` holds
  `roles/bigquery.dataEditor` and `roles/bigquery.jobUser` at project level, so it can read the
  export. No IAM change, no new secret: the job needs the five SMTP secrets the other jobs
  already carry.

### The entry-point contract, verbatim

`etf_quotes_main.py:55-62` — identical in all four jobs, and the shape to copy:

```python
    except Exception as exc:
        logger.exception("etf_quotes_main: pipeline failed")
        try:
            send_alert(exc)
            logger.info("etf_quotes_main: alert email sent")
        except Exception as alert_exc:
            logger.error("etf_quotes_main: failed to send alert: %s", alert_exc)
        sys.exit(1)
```

Import order is load-bearing and enforced by a recorded lesson
(`context/foundation/lessons.md:5-21`): `load_dotenv()` → `configure_logging()` → *then* `db.*`
and `src.*`, because `db/bigquery.py:52` reads `BIGQUERY_DATASET` at import time. This is what
requires the ruff E402 entry.

**`sys.exit(1)` reaches nobody.** Jobs run with `--max-retries=0`, and the Cloud Monitoring alert
policy `5888120520158610756` still has no notification channel attached
(`context/deployment/deploy-plan.md:66`). `send_alert` is the *entire* failure path to a human —
so the acceptance criterion "a failure raises through the existing alert path" is satisfied by
that try/except block and nothing else.

### The SMTP path

`src/notifier.py` — one private primitive, thin public wrappers:

- `_send(subject, body, html=False, to=None, from_name=None)` at `src/notifier.py:133-153`.
  Single-part `MIMEText`, never multipart; STARTTLS; `timeout=10`. Raises everything; each caller
  owns its try/except.
- `_smtp_creds()` at `:12-24` reads `SMTP_HOST/PORT/USER/PASSWORD/OWNER_EMAIL` via `os.environ[...]`
  — **`KeyError` on a missing secret**, deliberately loud — and strips BOM/CRLF that Secret Manager
  injects. Do not bypass it.
- Owner-facing mail is prefixed `[puls-gpw]` (only `send_alert` today); user-facing mail is
  prefixed `Faro —`. A cost report to the owner takes `[puls-gpw]`.
- **There is no shared HTML template and no table helper.** Five `_*_html()` builders each emit a
  full document; `grep '<table'` over `src/` returns zero. `_announcement_digest_html`
  (`:303-348`) is the closest model — Faro navy `#14304A` chrome, logo from
  `{base_url}/static/img/faro-mark.png`, all styling inline, and **every interpolated value через
  `html.escape(..., quote=True)`** (enforced by tests after an AI-sec finding on PR #159).

Two traps carried over from the mail layer:

- `msg["To"] = to or owner` (`:149`) silently routes a falsy recipient to the owner. Harmless
  here (we *want* `to=None`), but it means a misconfigured recipient would look like success.
- `send_alert` builds its body from `traceback.format_exc()`, which reads the *ambient* exception
  — called outside an `except` block it prints `NoneType: None` (`main.py:188` already does this).
  If the job ever alerts on a synthesized condition, put the detail in the exception message.

**Emptiness guard.** The mailer has no guards at all; the project's "never publish an empty
xpost" rule is enforced in the caller (`post_main.py:62-84`). Same applies here: a zero-row
billing query means the query broke, not that the day was free. The entry point must refuse to
send an all-zero report and raise instead — otherwise a broken query looks like a quiet, healthy
morning, which is the exact failure mode the daily cadence was chosen to eliminate.

### The BigQuery layer

- Client: lazy double-checked singleton with the `with_quota_project` guard already in place
  (`db/bigquery.py:89-113`).
- Table refs: **`_table_ref(client, table)`** at `:116-117` composes `f"{client.project}.{_DATASET}.{table}"`.
  There is not one hardcoded fully-qualified table literal anywhere in the repo — so add
  `_GCP_BILLING_EXPORT_TABLE_NAME = "gcp_billing_export_v1_01E214_63C8A3_9E57E3"` next to the other
  table-name constants and use the existing helper. **Do not** add a `_SCHEMA`, a
  `create_*_if_not_exists()` or an `ensure_*_schema_current()` for it — Google owns this table.
- Query shape: f-string interpolating **only** table refs and SQL fragments, all values bound as
  `ScalarQueryParameter` in a `QueryJobConfig`, `list(client.query(...).result())` in a `try`,
  re-raised as `BigQueryError(f"<fn> failed: {exc}") from exc`. Closest template:
  `get_previous_session_closes` (`db/bigquery.py:3895-3926`).
- **No query in this repo reads a REPEATED RECORD or STRUCT column from a table.** Every existing
  `UNNEST` unnests a *parameter* (`db/bigquery.py:931, 1375, 1444, 3040`). `LEFT JOIN UNNEST(credits)`
  will be the first — state the choice in the docstring, as this codebase does.
- Timezone: house convention is to name the zone **inside the SQL** (`DATE(ts, 'Europe/Warsaw')`,
  `db/bigquery.py:529, 947, 3129`) and assert the literal in a test, plus a module-level
  `WARSAW = ZoneInfo("Europe/Warsaw")` in the entry point for "which day is it". Both apply here:
  `usage_start_time` is a UTC instant, so bucketing without an explicit zone straddles days.

### Vertex AI by model — the SKU mapping and its trap

Live SKU descriptions for 2026-08-05:

| SKU | Gross PLN | usage.amount (tokens) |
|---|---:|---:|
| Gemini 2.5 Flash GA Text Input - Predictions | 1.2143 | 1,065,040 |
| Gemini 2.5 Flash GA Thinking Text Output - Predictions | 0.5977 | 62,906 |
| Gemini 2.5 Flash **Lite** Text Input - Predictions | 0.1077 | 283,548 |
| Gemini 2.5 Flash GA Text Output (Thinking On) - Predictions | 0.0725 | 7,632 |
| Gemini 2.5 Flash **Lite** Text Output - Predictions | 0.0189 | 12,485 |

**The trap: `"Gemini 2.5 Flash Lite"` contains `"Flash"`.** A naive
`WHEN sku.description LIKE '%Flash%' THEN 'gemini-2.5-flash'` swallows both models and the
breakdown silently collapses. The Lite branch must be tested **first**, and that ordering deserves
its own unit test — it is invisible in review and produces plausible-looking output when wrong.

Also note `Cloud Run` bills two SKUs in wildly different units (`seconds`, `byte-seconds`) — the
per-service table should show cost only, and reserve the token column for Vertex rows.

### Configurable threshold — house pattern

No settings module, no Pydantic. The convention is a module-level constant read from env with an
inline literal default: `src/http_client.py:12-14`, `src/parser.py:21-23`, `db/bigquery.py:52`.
So `_ANOMALY_FACTOR = float(os.environ.get("COST_ANOMALY_FACTOR", "2.0"))`, plus an entry in
`.env.example` (which has no cost vars today) and an explicit `--update-env-vars` in the deploy
step.

## Code References

- `etf_quotes_main.py:1-27, 55-62` — the entry-point template: import order and the failure block
- `src/notifier.py:12-24, 133-153, 383-396` — `_smtp_creds`, `_send`, `send_alert`
- `src/notifier.py:303-348` — `_announcement_digest_html`, the HTML model to copy
- `db/bigquery.py:52, 89-117` — `_DATASET`, lazy client, `_table_ref`
- `db/bigquery.py:3895-3926` — `get_previous_session_closes`, the read-query template
- `.github/workflows/deploy.yml:16-18, 86-92` — path filter, and the job step to paste
- `pyproject.toml:49-60` — the ruff E402 per-file-ignores that a new entry point must join
- `context/foundation/infra.md:100-132` — the human-only job + scheduler provisioning runbook
- `tests/test_bigquery_broker_operations.py:290-321` — asserting on the built SQL and its params
- `tests/test_notifier.py:13-24` — patching `src.notifier._send` to test a sender wrapper
- `tests/test_company_stats_main.py:89, 106-112` — patching `send_alert` on the *importing* module

## Architecture Insights

- **The mailer stays dumb; callers hold the policy.** No guards live in `src/notifier.py`;
  emptiness, publishability and recipient validity are all enforced upstream. The cost report
  follows suit — the zero-row guard belongs in `cost_report_main.py`.
- **Alerts must keep meaning "infra is broken."** `src/auth.py:421-425` deliberately declines to
  alert on a user-driven throttle, to protect that signal. The same bar applies here: a cost
  *anomaly* is a subject-line flag, never a `send_alert`. Only a query or send failure alerts.
- **Decision logic in Python, aggregation in SQL.** The project has been bitten twice by SQL that
  passed mocked tests and failed on real BigQuery (reserved keywords PUL-29, `REQUIRED` column
  PUL-88). Keeping the median and the threshold comparison in Python narrows the SQL to a plain
  `GROUP BY` and puts the interesting logic where tests actually run it.
- **Job creation is human, job update is CI.** A consistent split across every job so far, and the
  reason a plan for this change has a manual prerequisite that must happen *before* the merge.

## Historical Context (from prior changes)

- `context/archive/2026-08-06-vertex-ai-cost-verification/findings.md` (PUL-69) — the direct
  parent. Establishes the table, PLN currency, the credit that zeroes net, the `requests`-means-
  tokens caveat, and the 2026-08-05 spike this report is meant to catch next time. Its queries
  were **never committed as a script**, only as markdown — so this change writes them for real.
  Note its figure for 2026-08-05 was 2.02 PLN; the settled value is now **2.778 PLN** (+37 %),
  which is itself evidence for reporting MTD alongside a provisional D-1.
- `context/archive/2026-06-29-pul-67/plan.md` — the newest job-adding change (ETF quotes). Phase
  order to mirror: BigQuery layer → module → entry point → consumers → **CI/CD last**, with
  `gcloud run jobs create` + scheduler flagged as human-only pre-merge prerequisites (`:501-503`).
  It did **not** update `infra.md`, which is why that file is stale today.
- `context/archive/2026-06-25-daily-company-stats-snapshot-ingestion/plan.md:299-360` — a better
  model for the last phase: a dedicated "Deployment wiring" phase whose deliverables are the
  `deploy.yml` step **and** the two `infra.md` tables. Use this one, not PUL-67's.
- `context/foundation/lessons.md:5-21` — `load_dotenv()` before GCP imports; `:211-235` — mocked
  BQ tests do not validate SQL, so `scripts/test_bq_*.py` round-trips are a required manual step.

## Related Research

- `context/archive/2026-06-29-pul-67/research.md` — job wiring as it stood in June
- `context/archive/2026-07-21-email-notifications-delivery/research.md` — the SMTP/notifier layer
- `context/archive/2026-08-06-vertex-ai-cost-verification/findings.md` — the billing export itself

## Open Questions

1. **Anomaly factor: 2.0 or 2.5?** 2.0 fires twice in 21 days and would have caught the 08-05
   spike; 2.5 fires never in the same window. Recommendation is 2.0 behind
   `COST_ANOMALY_FACTOR`, but this is a taste call about tolerance for shrugs, and it is the one
   number the plan should settle explicitly rather than default into.
2. **Median in Python or in SQL?** Recommendation is Python (exact, unit-testable). The SQL
   self-join is verified working if the plan prefers to keep it in one query.
3. **Does the mail carry the Faro chrome or a plain ops layout?** Every existing HTML mail is
   user-facing and branded; this one is ops-facing and goes only to the owner, like the plain-text
   `send_alert`. Cheapest defensible answer: HTML with the Faro chrome for the table, since the
   builder is a copy-paste — but a plain-text report would also be in-convention.
4. **Should a `scripts/test_bq_billing_export.py` round-trip ship with it?** The convention says
   yes (`scripts/test_bq_etf_quotes.py` accompanied `etf_quotes_main.py`), with the caveat that
   the billing table cannot be copied to a throwaway — the round-trip has to be read-only against
   the real table.
5. **`infra.md` is stale by one job already.** `puls-gpw-etf-quotes` and its trigger were never
   added, and the prose says "wszystkie trzy joby". Fixing that as part of this change is a small
   detour that stops the doc drifting further — but it is scope the ticket did not ask for.
