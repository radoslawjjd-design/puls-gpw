# Email delivery for watchlist announcement notifications (PUL-81 slice b) — Implementation Plan

## Overview

Build the delivery half of PUL-81: a Cloud Run job (`notification_main.py`, ~every 5 min, 24/7) that finds new analyzed announcements for watched companies, joins `notification_subscriptions` (where `enabled`) with watchlists, and sends **one digest email per user** (bundling all their new qualifying announcements) via the existing Gmail SMTP. Delivery is deduplicated per `(user_id, announcement_id)` by a new `notification_sent_log` table so nobody is emailed the same announcement twice. This consumes the storage + settings shipped in slice (a).

## Current State Analysis

- Slice (a) shipped `notification_subscriptions(user_id, email, min_score, enabled, confirmed_at, updated_at)` (`db/bigquery.py:2642-2674`), the settings UI, and the invariant that **`enabled` is the authoritative opt-in flag**. `email` is denormalized onto that row → the delivery join needs no `users` read.
- **No delivery pipeline exists** — grep confirms no consumer of `notification_subscriptions`, no sent-log, no notification cron. Announcements are stored + scored but nothing emails on them.
- Scheduled jobs are **root-level `*_main.py` scripts** run as Cloud Run jobs via `uv run --no-dev python <file>.py` (`etf_quotes_main.py:1-66`, `company_stats_main.py`). Skeleton: `load_dotenv()` before any `db.*` import → `configure_logging()` → `main()` with one top-level `try/except → send_alert(exc) → sys.exit(1)`. **Each job bootstraps its own tables** (jobs don't run the FastAPI startup hook).
- Announcements (`db/bigquery.py:48-67`): `announcement_id` = `sha256(url)`, `ticker` NULLABLE, `published_at` (DAY partition), `analyzed_at` (NULL until analyzed), `analysis_approved` BOOL, `analysis_score` FLOAT64 (NULL when skipped/rejected). `analysis_score` is **~20-160, not 0-100** (`src/analyzer.py:221-229`); NULL never qualifies (`>=` drops NULLs).
- Watchlist (`db/bigquery.py:462-469`): keyed on `user_id` (canonical since PUL-74); **no reverse lookup** — query raw table `WHERE ticker = @ticker`.
- Email: `src/notifier._send(subject, body, html, to, from_name)` (`:133-153`) + branded HTML templates with `_html_escape(…, quote=True)` (PR #159). `send_alert(exc)` (`:302-315`) is the owner-failure path every job uses.

Full grounding: `context/changes/email-notifications-delivery/research.md`.

## Desired End State

A deployed Cloud Run job `puls-gpw-notifications`, triggered every ~5 min 24/7 by Cloud Scheduler, runs a delivery pass: for each user with `enabled = true`, it finds new (not-yet-sent) analyzed+approved announcements of their watched tickers whose `analysis_score >= min_score`, emails them a single Faro-branded digest listing each announcement (company (ticker), title, event type, link to Faro filtered by ticker), and records each `(user_id, announcement_id)` in `notification_sent_log`. Re-runs never double-send. A per-recipient send failure is logged, skipped (auto-retried next pass), and reported to the owner via `send_alert` at the end; it never blocks other recipients. Verified by: unit tests (BQ funcs + the job loop), and a manual end-to-end test (insert a dummy TOA announcement → run the job → both opted-in accounts receive the digest → remove the dummy row).

### Key Discoveries:

- Job entry-point skeleton: `etf_quotes_main.py:1-66` / `company_stats_main.py:30-85` (copy template); per-item error isolation `main.py:98-101`.
- Recipient join template: `list_announcements_for_watchlist` (`db/bigquery.py:1706-1766`, announcements INNER JOIN watchlist ON ticker) + score/approval predicates from `list_top_announcements_public` (`:1928-1938`).
- Dedup record: `add_watchlist_ticker` (`db/bigquery.py:1056-1086`, `INSERT … SELECT … WHERE NOT EXISTS`).
- Table create/ensure pattern: `notification_subscriptions` (`db/bigquery.py:2642-2674`).
- Email: `send_verification_email` wrapper pattern (`src/notifier.py:272-284`); `_send(..., to=<user>, from_name="Faro")`.
- Deploy: per-job `gcloud run jobs update … --command=uv --args="run,--no-dev,python,<main>.py"` in `.github/workflows/deploy.yml`; CI only `update`s, never `create`s.
- Runbook + cron table: `context/foundation/infra.md` (human-only `gcloud run jobs create` + `scheduler create`).
- Job/main test pattern: `tests/test_company_stats_main.py:37-59`, `tests/test_main.py:44-70` (monkeypatch collaborators in the main module's namespace); BQ-fn tests `tests/test_bigquery.py:54-90`.

## What We're NOT Doing

- No ESP / own-domain email infra (SES/Postmark/Resend, SPF/DKIM/DMARC) — existing Gmail SMTP is used; ESP is a future `/10x-infra-research` when volume grows.
- No `analysis_score` shown to users; no `structured_analysis` summary in the email.
- No per-announcement detail page on our site — the email links to the Faro announcements view filtered by ticker (no new route).
- No double opt-in / confirmation (settled in slice a — `enabled` is authoritative).
- No `min_score` UI (slice a stores it; the job reads it, default 0).
- No automated GCP resource creation — the Cloud Run job + Cloud Scheduler trigger are created by a human via the `infra.md` runbook (this change adds the runbook entry + the CI `update` step + the runnable code).
- No additional notification channels / subscription gating (future).

## Implementation Approach

Bottom-up, three cohesive phases, each following an existing in-repo pattern. BQ data layer (sent-log table + a recipient-select join + a record fn, unit-tested in isolation) → the branded digest email in `notifier.py` (unit-tested) → the `notification_main.py` job that ties them together (select → group by user → send digest → record, with continue-on-error + owner alert), plus the `deploy.yml` update step and the `infra.md` runbook entry. The GCP job/scheduler creation is a documented human step. After deploy, the user's dummy-TOA end-to-end test validates the whole chain.

## Critical Implementation Details

- **Idempotency ordering — send-then-record.** For each user, send the digest first, then `record_notification_sent` each included `(user_id, announcement_id)` via `INSERT … WHERE NOT EXISTS`. A crash between send and record re-sends (a smaller digest) next pass — a rare, benign duplicate, preferred over a silent miss. The `WHERE NOT EXISTS` makes the record write itself idempotent.
- **The job creates its own tables.** `notification_main.py` must call `create_notification_sent_log_table_if_not_exists()` + `ensure_notification_sent_log_schema_current()` (and defensively the subscriptions creators) at the top of `main()` — it does NOT go through `src/api.py`'s startup hook.
- **App base URL for the email link/logo.** A cron job has no request origin. The digest email needs a configured base URL (new env `APP_BASE_URL`, default `https://gpw.okiem.ai`) to build the per-ticker Faro link and the logo `src`. This must be added to the job's env in the runbook.
- **Candidate pre-filter window.** The recipient query filters `published_at >= @cutoff` (hits the DAY partition, cheap) where `cutoff = now - 48h`; the sent-log anti-join guarantees exactly-once regardless, so 48h only bounds cost and comfortably exceeds scrape+analyze latency.

## Phase 1: BigQuery data layer — sent-log + recipient select + record

### Overview

Add the `notification_sent_log` table and the two query functions the job needs: select-pending (the composite join) and record-sent (idempotent insert). Unit-test with a mocked BQ client.

### Changes Required:

#### 1. Sent-log table + DDL helpers

**File**: `db/bigquery.py`

**Intent**: A dedup table keyed on `(user_id, announcement_id)`, self-provisioning like every other table.

**Contract**: `_NOTIFICATION_SENT_LOG_TABLE_NAME = "notification_sent_log"`, `_NOTIFICATION_SENT_LOG_SCHEMA` = `user_id` STRING REQUIRED, `announcement_id` STRING REQUIRED, `email` STRING NULLABLE, `sent_at` TIMESTAMP REQUIRED. `create_notification_sent_log_table_if_not_exists()` + `ensure_notification_sent_log_schema_current()` mirroring `notification_subscriptions` (`:2657-2674`). (No partitioning needed; optional DAY-partition on `sent_at` for cost.)

#### 2. Recipient select (the composite join)

**File**: `db/bigquery.py`

**Intent**: Return every not-yet-notified `(user, announcement)` pair that qualifies, so the job can group by user and send.

**Contract**: `select_pending_notifications(candidate_cutoff: datetime) -> list[dict]` — parameterized join `announcements a JOIN watchlist w ON w.ticker = a.ticker JOIN notification_subscriptions ns ON ns.user_id = w.user_id`, filtered `a.analysis_approved = TRUE AND a.analysis_score IS NOT NULL AND ns.enabled = TRUE AND ns.email IS NOT NULL AND a.analysis_score >= COALESCE(ns.min_score, 0) AND a.published_at >= @candidate_cutoff AND a.published_at >= COALESCE(ns.confirmed_at, ns.updated_at) AND NOT EXISTS (SELECT 1 FROM notification_sent_log l WHERE l.user_id = ns.user_id AND l.announcement_id = a.announcement_id)`. Returns rows with `user_id, email, announcement_id, ticker, company, title, event_type` (ordered by `user_id, published_at`). `BigQueryError`-wrapped. Template: `list_announcements_for_watchlist` (`:1706-1766`) + predicates from `list_top_announcements_public` (`:1928-1938`).

> **Since-opt-in scope (F1):** the `a.published_at >= COALESCE(ns.confirmed_at, ns.updated_at)` predicate ensures a user is only notified about announcements published **after they opted in** — never a backlog blast of the whole 48h candidate window on first enable. `confirmed_at` is stamped on enable in slice (a) (non-NULL whenever `enabled = true`); `updated_at` is the fallback. This is a per-user floor stacked on top of the global `@candidate_cutoff` partition prune.

#### 3. Record-sent (idempotent)

**File**: `db/bigquery.py`

**Intent**: Mark a `(user, announcement)` pair delivered; safe to call twice.

**Contract**: `record_notification_sent(user_id: str, announcement_id: str, email: str | None) -> None` — `INSERT INTO notification_sent_log (…) SELECT @user_id, @announcement_id, @email, CURRENT_TIMESTAMP() FROM (SELECT 1) WHERE NOT EXISTS (SELECT 1 FROM notification_sent_log WHERE user_id = @user_id AND announcement_id = @announcement_id)`. Template `add_watchlist_ticker` (`:1056-1086`). `BigQueryError`-wrapped.

#### 4. Unit tests

**File**: `tests/test_bigquery.py`

**Intent**: Verify the SQL functions against a mocked BQ client.

**Contract**: `patch("db.bigquery._get_client", …)` with `_mock_bq_client_with_rows(...)` for `select_pending_notifications` (assert join/filters present in the query string + params, and row mapping) and `_mock_bq_client(affected_rows=…)` for `record_notification_sent` (assert `NOT EXISTS` guard + params); `BigQueryError` on client failure; a `create_*_creates_on_not_found` test mirroring the companies/subscriptions test.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_bigquery.py -q`
- Full suite green: `uv run pytest --tb=short`

#### Manual Verification:

- `select_pending_notifications` with an empty mocked result returns `[]` without raising.

**Implementation Note**: After automated verification passes, pause for human confirmation before Phase 2.

---

## Phase 2: Branded digest email

### Overview

Add the Faro-branded digest email (template + sender) to `notifier.py`, reusing `_send`. Unit-test the sender wiring + escaping.

### Changes Required:

#### 1. Digest template + sender

**File**: `src/notifier.py`

**Intent**: Render a per-user digest listing their new announcements and send it via existing SMTP.

**Contract**:
- `_announcement_digest_html(items: list[dict], base_url: str) -> str` — Faro-branded HTML (navy header + logo `f"{base_url}/static/img/faro-mark.png"`, same shell as `_verification_html` `:227-269`); one list entry per item showing **company (ticker) — title — event_type**, each linking to `f"{base_url}/?view=announcements&ticker={ticker}"`. **HTML-escape every interpolated field** (`_html_escape(…, quote=True)`, PR #159) — including the ticker inside the URL.
- `send_announcement_digest_email(to_email: str, items: list[dict], base_url: str) -> None` — `_send(subject, _announcement_digest_html(items, base_url), html=True, to=to_email, from_name="Faro")`, subject e.g. `f"Faro — {n} nowych komunikatów Twoich spółek"` (singular/plural aware). Raises on SMTP failure (caller decides recovery), mirroring `send_verification_email` (`:272-284`).

#### 2. Unit test

**File**: `tests/test_notifier.py` (add; create if absent)

**Intent**: Verify the sender calls `_send` with the right args and that fields are escaped + linked.

**Contract**: `patch("src.notifier._send")`; assert called once with `html=True`, `to=<email>`, `from_name="Faro"`, subject count matches `len(items)`; render `_announcement_digest_html` with an item containing HTML-special chars and assert they're escaped and the `?view=announcements&ticker=` link is present.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_notifier.py -q`
- Full suite green: `uv run pytest --tb=short`

#### Manual Verification:

- Render the digest HTML for 1 and 3 items locally; subject pluralizes correctly; links point at `{base_url}/?view=announcements&ticker=…`.

**Implementation Note**: After automated verification passes, pause for human confirmation before Phase 3.

---

## Phase 3: Delivery job + deploy wiring + runbook

### Overview

Add `notification_main.py` (the runnable pass), wire it into CI (`deploy.yml` `jobs update`), and document the one-time GCP creation in `infra.md`. The GCP job/scheduler creation is a human step.

### Changes Required:

#### 1. Delivery job entry point

**File**: `notification_main.py` (new, repo root)

**Intent**: The runnable pass: bootstrap tables → select pending → group by user → send one digest per user (continue-on-error) → record each pair → alert owner if any failures.

**Contract**: Mirror `etf_quotes_main.py` skeleton — `load_dotenv()` before `db.*` imports, `configure_logging()`, `main()` in one `try/except Exception → send_alert(exc) → sys.exit(1)`. In `main()`: `create_notification_sent_log_table_if_not_exists()` + `ensure_...` (+ defensively subscriptions creators); `rows = select_pending_notifications(now - 48h)`; if empty, log + return (exit 0); group rows by `user_id`; for each user, `try: send_announcement_digest_email(email, items, APP_BASE_URL); for each item: record_notification_sent(user_id, announcement_id, email)` — on a per-user `Exception`, log it, mark a `had_failures` flag, and `continue` (do NOT record → auto-retry next pass); after the loop, if `had_failures`, call `send_alert(...)` with a summary (do not `exit(1)` — partial success is success). Read `APP_BASE_URL` via `os.environ.get("APP_BASE_URL", "https://gpw.okiem.ai")`. Optional `--dry-run` arg (log recipients, skip send/record) for safe prod verification.

> **Exception boundary (F2):** exactly one boundary is fatal — a failure of the initial `select_pending_notifications` (or the table-bootstrap calls) means the pass can't run → it propagates to the outer handler (`send_alert` + `sys.exit(1)`). **Everything inside the per-user loop — both `send_announcement_digest_email` and every `record_notification_sent` — is caught by the per-user `except Exception` (including `BigQueryError`): log, set `had_failures`, `continue`.** One user's transient send/record failure must never block the remaining recipients; the un-recorded pairs are auto-retried next pass. Only unrecorded pairs re-send, so a mid-user record failure re-sends at most that user's digest once.

#### 2. CI deploy step

**File**: `.github/workflows/deploy.yml`

**Intent**: Update the notification job image on every deploy (never create).

**Contract**: Add a `gcloud run jobs update puls-gpw-notifications --image="${IMAGE}:${sha}" --command=uv --args="run,--no-dev,python,notification_main.py" --region=${REGION} --project=${PROJECT_ID}` step, mirroring the etf-quotes step. The job's secrets/env (SMTP_*, OWNER_EMAIL, APP_BASE_URL, GOOGLE_CLOUD_PROJECT, BIGQUERY_DATASET) are set once at `create` time (runbook) and persist across `update`s.

#### 3. Runbook entry

**File**: `context/foundation/infra.md`

**Intent**: Document the human-only one-time creation of the job + scheduler, and add the new cron row.

**Contract**: Add a Cloud Scheduler table row (`puls-gpw-notifications-trigger`, `*/5 * * * *`, Warsaw, 24/7) and a "One-time provisioning runbook — puls-gpw-notifications" section with the exact `gcloud run jobs create puls-gpw-notifications … --command=uv --args="run,--no-dev,python,notification_main.py" --set-secrets="SMTP_HOST=…,SMTP_PORT=…,SMTP_USER=…,SMTP_PASSWORD=…,OWNER_EMAIL=…" --set-env-vars="GOOGLE_CLOUD_PROJECT=puls-gpw,BIGQUERY_DATASET=espi_ebi,APP_BASE_URL=https://gpw.okiem.ai" --service-account=puls-gpw-runner@…` + the `gcloud scheduler jobs create http puls-gpw-notifications-trigger …:run` command. Mark both as human-only.

#### 4. Job unit test

**File**: `tests/test_notification_main.py` (new)

**Intent**: Verify the loop wiring, grouping, idempotency, and error handling without real BQ/SMTP.

**Contract**: `import notification_main`; monkeypatch `notification_main.{create_*, ensure_*, select_pending_notifications, send_announcement_digest_email, record_notification_sent, send_alert}` with MagicMocks (+ `sys.argv`). Tests: (a) happy path — 2 users × their items → `send_announcement_digest_email` called once per user, `record_notification_sent` called per pair, no `send_alert`, exit 0; (b) empty pending → 0 sends, exit 0; (c) one user's send raises → other users still sent, failing user's pairs NOT recorded, `send_alert` called once, exit 0; (d) `select_pending_notifications` raises `BigQueryError` → `send_alert` + `SystemExit(1)`. Mirror `tests/test_company_stats_main.py`.

### Success Criteria:

#### Automated Verification:

- Job unit tests pass: `uv run pytest tests/test_notification_main.py -q`
- Full suite green: `uv run pytest --tb=short`

#### Manual Verification:

- **[human-only GCP]** Run the `infra.md` runbook to `create` the Cloud Run job + Cloud Scheduler trigger with the SMTP/APP_BASE_URL env.
- Deploy (merge to master) updates the job image without error.
- **End-to-end test**: insert a dummy TOA (Toya) announcement into BQ (approved, scored, recent `published_at`/`analyzed_at`), run `puls-gpw-notifications` (or `notification_main.py --dry-run` first), confirm both opted-in accounts receive the digest email, then remove the dummy row. A second run sends nothing (dedup).

**Implementation Note**: After automated verification passes, pause for human confirmation. This is the final phase; the GCP create + end-to-end test are human steps.

---

## Testing Strategy

### Unit Tests:

- `db.bigquery` — `select_pending_notifications` (join/filters/params, row mapping, empty→[]), `record_notification_sent` (NOT EXISTS guard, params), sent-log create-on-NotFound, `BigQueryError` wrapping.
- `src.notifier` — `send_announcement_digest_email` wiring (`_send` args, subject pluralization) + `_announcement_digest_html` escaping + ticker link.
- `notification_main` — happy path, empty, per-user send failure (continue + alert, no record), BQ failure (alert + exit 1). Monkeypatch collaborators in the main namespace.

### Integration / Manual Tests:

- The dummy-TOA end-to-end test (human) after the job is created + deployed.

### Manual Testing Steps:

1. (human GCP) Create job + scheduler per the `infra.md` runbook.
2. `notification_main.py --dry-run` (or a real run) after inserting a dummy TOA announcement → verify recipients logged / emails received on both accounts.
3. Re-run → confirm zero sends (sent-log dedup).
4. Remove the dummy announcement row.

## Performance Considerations

Trivial at current volume. The recipient query is partition-pruned (`published_at >= now-48h`) + anti-joined; runs ~288×/day but each pass is a small join. Gmail SMTP limits (~500/day free, ~2000/day Workspace) are far from binding now; note for scale (ESP is the future fix). Digest bundling further reduces email count.

## Migration Notes

`notification_sent_log` self-provisions via the job's own `create/ensure` calls (additive, cold-start-safe) — no manual migration. New env `APP_BASE_URL` must be set on the job at create time (runbook). `analysis_score` NULLs are excluded by the `>=` filter (desired). `min_score` default 0 admits all approved announcements.

## References

- Research: `context/changes/email-notifications-delivery/research.md`
- Slice (a): `context/archive/2026-07-21-email-notifications-settings/`
- Job skeleton: `etf_quotes_main.py:1-66`; join template: `db/bigquery.py:1706-1766`; dedup: `db/bigquery.py:1056-1086`; email: `src/notifier.py:272-284`; runbook: `context/foundation/infra.md`; deploy: `.github/workflows/deploy.yml`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: BigQuery data layer — sent-log + recipient select + record

#### Automated

- [x] 1.1 Unit tests pass: `uv run pytest tests/test_bigquery.py -q` — 633bf26
- [x] 1.2 Full suite green: `uv run pytest --tb=short` — 633bf26

#### Manual

- [x] 1.3 `select_pending_notifications` with empty mocked result returns `[]` without raising — 633bf26

### Phase 2: Branded digest email

#### Automated

- [x] 2.1 Unit tests pass: `uv run pytest tests/test_notifier.py -q`
- [x] 2.2 Full suite green: `uv run pytest --tb=short`

#### Manual

- [x] 2.3 Digest HTML renders for 1 and 3 items; subject pluralizes; links point at `{base_url}/?view=announcements&ticker=…`

### Phase 3: Delivery job + deploy wiring + runbook

#### Automated

- [ ] 3.1 Job unit tests pass: `uv run pytest tests/test_notification_main.py -q`
- [ ] 3.2 Full suite green: `uv run pytest --tb=short`

#### Manual

- [ ] 3.3 (human-only GCP) Cloud Run job + Cloud Scheduler trigger created per the infra.md runbook with SMTP/APP_BASE_URL env
- [ ] 3.4 Deploy (merge to master) updates the job image without error
- [ ] 3.5 End-to-end: dummy TOA announcement → run job → both opted-in accounts receive the digest → re-run sends nothing → remove dummy row
