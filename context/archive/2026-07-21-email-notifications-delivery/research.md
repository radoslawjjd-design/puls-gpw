---
date: 2026-07-21T15:17:21+02:00
researcher: Radek
git_commit: 484f4e24118cfe60bbdd308f23d65de6c736556a
branch: master
repository: radoslawjjd-design/puls-gpw
topic: "Email delivery for watchlist announcement notifications (PUL-81 slice b)"
tags: [research, codebase, notifications, delivery, cron, bigquery, smtp, dedup, sent-log, cloud-run-job]
status: complete
last_updated: 2026-07-21
last_updated_by: Radek
---

# Research: Email delivery for watchlist announcement notifications (PUL-81 slice b)

**Date**: 2026-07-21T15:17:21+02:00
**Researcher**: Radek
**Git Commit**: 484f4e24118cfe60bbdd308f23d65de6c736556a
**Branch**: master
**Repository**: radoslawjjd-design/puls-gpw

## Research Question

How to build the delivery half of PUL-81 (slice b): a runnable pass that finds new announcements for watched companies, joins `notification_subscriptions` (where `enabled`) with watchlists, sends one branded email per user via existing SMTP when `analysis_score >= min_score`, and dedups via a sent-log so nobody gets the same announcement twice. Cloud Run job + scheduler *creation* is a human-only step; this change delivers the runnable code + tables.

## Summary

Every building block has a close, copyable precedent, and slice (a) already left `notification_subscriptions` + `min_score` + the SMTP layer in place. The shape is:

- **Entry point**: a new root-level `notification_main.py` (mirrors `etf_quotes_main.py` / `company_stats_main.py`) run as a Cloud Run job via `uv run --no-dev python notification_main.py`. `load_dotenv()` before any `db.*` import; one top-level `try/except → send_alert(exc) → sys.exit(1)`; **the job creates its own tables** (jobs don't go through the API's `create_app` startup hook).
- **New table**: `notification_sent_log(user_id, announcement_id, email, sent_at)` — the dedup key `(user_id, announcement_id)` enforced by an `INSERT … WHERE NOT EXISTS` (BQ has no unique constraint). Same create/ensure/startup pattern as every other table.
- **Recipient query**: a 3-table join `announcements × watchlist × notification_subscriptions` (template: `list_announcements_for_watchlist` + score/approval predicates from `list_top_announcements_public`), filtered `enabled = TRUE AND analysis_approved = TRUE AND analysis_score IS NOT NULL AND analysis_score >= COALESCE(min_score, 0) AND email IS NOT NULL`, anti-joined against `notification_sent_log`.
- **Send**: reuse `src/notifier._send`; add a branded `_announcement_html` + `send_announcement_email(to_email, …)` wrapper (HTML-escape every embedded field — PR #159). `send_alert` on failure.
- **Idempotency**: **send-then-record** with the `INSERT … WHERE NOT EXISTS` as the dedup key, per-item loop (send → immediately record) to bound the crash window. Rare double-send is preferred over a silent miss (matches the X-poster philosophy).
- **Watermark**: prefer the sent-log anti-join for exactly-once (a pure `published_at`/`analyzed_at` watermark is racy against async analysis). Cheap hybrid: pre-filter candidate announcements by `published_at >= now - N` (hits the DAY partition) then anti-join the sent-log.

**Biggest decisions for planning:** (1) delivery infra — existing Gmail SMTP is fine for the current 2-3 accounts (the user's test); ESP + authenticated own-domain is a *scaling* decision to defer (or split to `/10x-infra-research`). (2) schedule window — 24/7 (like the `*/15` scraper) vs market-hours `*/5 9-17 * * 1-5` Warsaw. (3) candidate-window size N for the pre-filter.

## Detailed Findings

### Entry-point / Cloud Run job pattern

Scheduled jobs are **root-level `*_main.py` scripts** run via `uv run --no-dev python <file>.py`. Canonical skeleton (`etf_quotes_main.py:1-66`, mirrored by `company_stats_main.py`, `post_main.py`, `main.py`):
- `from dotenv import load_dotenv; load_dotenv()` at line ~9, **before** any `db.*` import — `BIGQUERY_DATASET`/`GOOGLE_CLOUD_PROJECT` are read at import time (`.claude/rules/db-bigquery.md`, lessons.md "GCP client init").
- `from src.logging_setup import configure_logging; configure_logging()` (`src/logging_setup.py:8-29`, JSON formatter for Cloud Logging), then `logger = logging.getLogger(__name__)`.
- `def main()` with one top-level `try/except Exception as exc: logger.exception(...); send_alert(exc); sys.exit(1)`. Success returns normally (exit 0). `post_main.py:234` shows an intentional early `sys.exit(0)` when there's nothing to do.
- **Each main bootstraps its own tables** at the top of the try-block — `main.py:44-48`, `company_stats_main.py:30-31`, `etf_quotes_main.py:32-35`, `post_main.py:244-247`. Jobs do **not** run the FastAPI `@app.on_event("startup")` hook (`src/api.py:316-329`), so `notification_main.py` must call `create_notification_sent_log_table_if_not_exists()` + `ensure_...` (and `create_notification_subscriptions_table_if_not_exists()` defensively — idempotent) itself.
- Per-item processing loop with error isolation: `BigQueryError` re-raises to the outer handler; other per-item exceptions are logged and the loop continues (`main.py:98-101`).
- Args: `etf_quotes_main.py`/`company_stats_main.py` take none. `main.py:34-39` / `post_main.py:220-227` use argparse — add a `--dry-run` / `--window-minutes` only if wanted.

### Deploy + scheduler wiring

- `.github/workflows/deploy.yml` (push to master): one shared image `europe-central2-docker.pkg.dev/puls-gpw/puls-gpw/puls-gpw:${{ github.sha }}` built from the shared `Dockerfile` (python:3.13-slim + `uv sync --frozen --no-dev`). Each job gets a `gcloud run jobs update <job> --image=…:${sha} --command=uv --args="run,--no-dev,python,<main>.py" --region … --project …` step. Jobs needing extra secrets add them additively (`--update-secrets`), e.g. the `puls-gpw-post` step carries SMTP + X secrets.
- **To add the notification job**: (a) human-only `gcloud run jobs create puls-gpw-notifications … --command=uv --args="run,--no-dev,python,notification_main.py" --set-secrets="SMTP_*,OWNER_EMAIL" --set-env-vars="GOOGLE_CLOUD_PROJECT,BIGQUERY_DATASET" --service-account=puls-gpw-runner@…`, then (b) add a `gcloud run jobs update puls-gpw-notifications …` step to `deploy.yml`. The CI workflow only ever `update`s, never `create`s (precedent: `context/archive/2026-06-29-pul-67/plan.md:501`).
- **Runbook + cron table live in `context/foundation/infra.md`** — the "One-time provisioning runbook — puls-gpw-company-stats" section is the exact template. Constants: project `puls-gpw`, region `europe-central2`, SA `puls-gpw-runner@puls-gpw.iam.gserviceaccount.com` (already has secretAccessor + BQ + run.admin). All schedulers use `--time-zone="Europe/Warsaw"`.
- Existing crons (infra.md): scraper `puls-gpw-trigger` `*/15 * * * *` (24/7), company-stats `1,31 9-17 * * 1-5` (market hours). A ~5-min notification trigger would be a new infra.md row: `*/5 9-17 * * 1-5` (market hours) **or** 24/7 to match the scraper — a planning decision.

### Announcements data model + "what's new"

- `announcements` table (`db/bigquery.py:48-67`), DAY-partitioned on `published_at` (`:130`). Relevant columns: `announcement_id` STRING REQ (= `sha256(url)`, `announcement_id_for_url` `:112-114`, deterministic — no DB round-trip), `ticker` STRING NULLABLE (NULL if parse failed), `company`, `published_at` TIMESTAMP REQ, `title` REQ, `event_type`, **`analyzed_at` TIMESTAMP NULLABLE** (NULL until analyzed — stamped by `save_analysis_result`), **`analysis_approved` BOOL NULLABLE**, **`analysis_score` FLOAT64 NULLABLE**, `structured_analysis` (JSON string).
- **No boolean `processed` flag** — "analyzed" = `analyzed_at IS NOT NULL`. **No notification cursor / sent-log exists anywhere** (confirmed by grep).
- Existing new-detection: `get_processed_ids_since(cutoff) -> set[str]` (`db/bigquery.py:2197-2212`, `SELECT announcement_id WHERE published_at >= @cutoff`) used by the scraper with a 2× window safety margin (`src/scraper.py:48-50`). This dedups *scraping*, not *notification*.
- **`analysis_score` semantics** (`src/analyzer.py:_compute_score` 221-229): `tier_bonus (0-40) + event_score (20-100) + priority_bonus (0/20)` → effective range **~20-160, NOT 0-100, not clamped**. Set only when `approved` is truthy; NULL when analyzer skipped (no ticker/parsed_content), LLM failed, or the gate rejected. **NULL never qualifies** — SQL `analysis_score >= @min_score` drops NULLs (comparison → UNKNOWN), which is the desired semantics (`list_top_announcements_public` also adds explicit `analysis_score IS NOT NULL`, `:1933`). `min_score` default 0 admits every approved+scored announcement.

### The delivery join

- **watchlist** (`_WATCHLIST_SCHEMA` `:462-469`): `client_id` (legacy, DROP pending PUL-88), `ticker` REQ, `added_at`, **`user_id` STRING NULLABLE (canonical since PUL-74)**. Only forward helpers exist (user_id → tickers); **no reverse lookup** — query the raw table: `SELECT user_id FROM watchlist WHERE ticker = @ticker`.
- **notification_subscriptions** (`_NOTIFICATION_SUBSCRIPTIONS_SCHEMA` `:2644-2654`, shipped in slice a): `user_id` REQ, `email` NULLABLE, `min_score` INT64 NULLABLE, `enabled` BOOL REQ (authoritative opt-in), `confirmed_at`, `updated_at`. `email` lives on this row → no `users` join needed. Reader `get_notification_settings(user_id)` (`:2677-2708`) is single-user; **no bulk/all-subscribers read exists** — slice b adds the composite join.
- **SQL template** — `list_announcements_for_watchlist` (`:1706-1766`, announcements INNER JOIN watchlist-subquery ON ticker) + `list_top_announcements_public` (`:1928-1938`, the `analysis_approved = TRUE AND analysis_score IS NOT NULL` predicates). Composite recipient query:
  ```sql
  SELECT ns.user_id, ns.email, a.announcement_id, a.ticker, a.title, a.analysis_score
  FROM `…announcements` a
  JOIN `…watchlist` w                 ON w.ticker = a.ticker
  JOIN `…notification_subscriptions` ns ON ns.user_id = w.user_id
  WHERE a.analysis_approved = TRUE AND a.analysis_score IS NOT NULL
    AND ns.enabled = TRUE AND ns.email IS NOT NULL
    AND a.analysis_score >= COALESCE(ns.min_score, 0)
    AND a.published_at >= @candidate_cutoff        -- partition pre-filter (cheap)
    AND NOT EXISTS (SELECT 1 FROM `…notification_sent_log` l
                    WHERE l.user_id = ns.user_id AND l.announcement_id = a.announcement_id)
  ```
  `min_score` INT64 vs `analysis_score` FLOAT64 — BQ coerces, fine. Guard `email IS NOT NULL` before sending.
- **ROW_NUMBER/CURRENT_DATE gotcha does NOT apply** — that rule is `company_daily_stats`-specific (per-ticker/day sparsity). The delivery join touches announcements + watchlist + subscriptions only; score lives on the announcement row.

### Sent-log dedup table

- Follow the four-part pattern (`_NAME` const + `_SCHEMA` + `create_*_if_not_exists` + `ensure_*_schema_current`), copying `notification_subscriptions` (`:2642-2674`) or the partition-less `x_posts` creator (`:136-146`). Suggested `notification_sent_log(user_id STRING REQ, announcement_id STRING REQ, email STRING NULLABLE, sent_at TIMESTAMP REQ)` — optionally DAY-partition on `sent_at` for cost.
- **Record (insert-if-not-exists)** — template `add_watchlist_ticker` (`:1056-1086`):
  ```sql
  INSERT INTO `…sent_log` (user_id, announcement_id, email, sent_at)
  SELECT @user_id, @announcement_id, @email, CURRENT_TIMESTAMP()
  FROM (SELECT 1)
  WHERE NOT EXISTS (SELECT 1 FROM `…sent_log`
                    WHERE user_id = @user_id AND announcement_id = @announcement_id)
  ```
  Atomic, self-deduplicating (re-run = 0 rows). Standard `job.result(); if job.errors: raise BigQueryError(...)` envelope.
- **Select-not-yet-notified** — anti-join precedent `list_tickers_missing_from_companies` (`:2167`, `NOT EXISTS`). This is folded into the composite recipient query above.

### Email send

- `src/notifier._send(subject, body, html=False, to=None, from_name=None)` (`:133-153`) — `to=<user>` for user-facing mail; `from_name="Faro"` sets display name only (address stays `SMTP_USER`; Gmail rewrites mismatched From). Branded templates `_verification_html`/`_password_reset_html` (`:183-269`) with `_html_escape(…, quote=True)` on every interpolated value (PR #159). Public senders `send_verification_email`/`send_password_reset_email` (`:272-299`) are the wrapper pattern.
- **Add** `_announcement_html(…)` (branded, HTML-escaped) + `send_announcement_email(to_email, …announcement fields…)` = `_send(subject, _announcement_html(...), html=True, to=to_email, from_name="Faro")`. Owner-failure path `send_alert(exc)` (`:302-315`).

### Idempotency / re-run safety

- Precedents: X-poster **record-then-act** (`post_main.py:295-301`) — but that needs the row id *before* publishing; `x_post_already_published` (`:2073-2105`) is a documented check-then-act guard ("acceptable given one scheduler trigger per window"); `announcement_id` deterministic hash = natural scrape idempotency.
- **For the sent-log: send-then-record**, with `INSERT … WHERE NOT EXISTS` as the dedup key. Rationale: a crash between send and record → next run re-sends once (rare **double-send**); the reverse ordering risks a **silent miss** (worse for a benign notification). Per-item loop (send → immediately record) bounds a crash to the single in-flight pair. Optional belt-and-braces: re-check `NOT EXISTS` right before each send (same accepted race as the X-poster).

### Testing

- **Main unit test** (no TestClient): `import notification_main` then `monkeypatch.setattr(notification_main, "<collaborator>", MagicMock())` for each top-level import (table creators, the recipient-select fn, `send_announcement_email`, the record fn, `send_alert`) — names live in the main module's namespace. Precedent: `tests/test_company_stats_main.py:37-59`, `tests/test_main.py:44-70` (patches `sys.argv` too), `tests/test_post_main.py`.
- **BQ query-fn unit test**: `patch("db.bigquery._get_client", return_value=_mock_bq_client(...))` with helpers `_mock_bq_client(affected_rows)` / `_mock_bq_client_with_rows(rows)` (`tests/test_bigquery.py:54-90`) — for the new `select_recipients_not_notified` / `record_notification_sent` SQL.
- **SMTP mocking**: patch the send fn at its caller module (`notification_main.send_announcement_email`), never `smtplib`; pair with `patch(... .send_alert)` for failure paths (precedent `tests/test_auth_api.py:82, 464-565`).
- **Test matrix**: happy path (select → send → record in order, no alert); dedup on re-run (recipient query empty → 0 sends); partial send failure → `send_alert` + `SystemExit(1)`; BQ failure → alert + exit 1.

## Code References

- `etf_quotes_main.py:1-66`, `company_stats_main.py:30-85` — Cloud Run job entry-point skeleton (copy template)
- `main.py:33-113` — per-item processing loop with error isolation
- `src/logging_setup.py:8-29` — `configure_logging`
- `.github/workflows/deploy.yml` — per-job `gcloud run jobs update` steps
- `context/foundation/infra.md` — Cloud Run jobs + scheduler tables + one-time provisioning runbook (human-only step source)
- `db/bigquery.py:48-67` — announcements schema; `:112-114` `announcement_id_for_url`; `:2197-2212` `get_processed_ids_since`
- `db/bigquery.py:462-469` watchlist; `:2642-2674` notification_subscriptions (create/ensure/read); `:1706-1766` join template; `:1928-1938` score predicates; `:1056-1086` INSERT…WHERE NOT EXISTS; `:2167` anti-join
- `src/analyzer.py:221-229,35-43,286` — `analysis_score` computation + NULL cases
- `src/notifier.py:133-153,183-299,302-315` — `_send`, branded templates, senders, `send_alert`
- `tests/test_company_stats_main.py:37-59`, `tests/test_main.py:44-70`, `tests/test_bigquery.py:54-90` — job + BQ test patterns

## Architecture Insights

- **Jobs are standalone processes** — no shared startup hook; each `*_main.py` bootstraps its own tables and owns its own `try/except → send_alert → exit(1)`. The notification job is one more of these.
- **Deterministic ids + INSERT-WHERE-NOT-EXISTS = idempotency without locks** — the codebase leans on at-most-once-extra guards under a single scheduler trigger, not transactions.
- **`analysis_score` is not a 0-100 scale** (~20-160) — surface this if any UI ever shows `min_score`; for slice b the default 0 admits everything approved.
- **`email` is denormalized onto `notification_subscriptions`** (slice a) — the delivery join needs no `users` table read.
- **Lessons priors that apply** (`context/foundation/lessons.md`): `load_dotenv` before db imports (satisfied by the main skeleton); the new-SPA-view lesson is not relevant here (no UI). No new GCP *client* is introduced (reuse existing BQ + SMTP), so the `with_quota_project` guard is already handled.

## Historical Context (from prior changes)

- `context/archive/2026-07-21-email-notifications-settings/` — slice (a): shipped `notification_subscriptions`, the settings UI, and the decision that `enabled` is the authoritative opt-in flag while `confirmed_at` is informational (the delivery cron must filter on `enabled`, per that change's Migration Notes). Its research also flagged the ESP/own-domain deliverability concern as a slice-b infra decision.
- `context/archive/2026-06-27-company-stats-upsert/` and `2026-06-25-daily-company-stats-snapshot-ingestion/` — the scheduled-job + MERGE-upsert + market-hours-cron precedent.
- `context/archive/2026-06-29-pul-67/plan.md:501` — "CI workflow only `update`s a job, never `create`s"; the etf-quotes precedent for adding a new job to CI.

## Related Research

- `context/archive/2026-07-21-email-notifications-settings/research.md` — the slice-a companion (settings + storage + SMTP layer).

## Open Questions

1. **Delivery infra — Gmail SMTP vs ESP.** Existing `src/notifier` (Gmail SMTP) is fine for the current 2-3 accounts (the user's test). ESP + authenticated own-domain (SES/Postmark/Resend + SPF/DKIM/DMARC) is a *scaling* decision. **Recommendation:** ship slice b on the existing SMTP; treat ESP as a separate `/10x-infra-research` when user volume grows. Not a blocker for the test.
2. **Schedule window.** 24/7 (`*/5 * * * *`, matching the 24/7 `*/15` scraper) vs market-hours (`*/5 9-17 * * 1-5` Warsaw, matching company-stats). Announcements can arrive outside market hours; a notification is time-sensitive → leaning 24/7 (or a wider window). Decide in planning.
3. **Candidate pre-filter window N.** `published_at >= now - N` bounds the join cheaply (partition-friendly) while the sent-log guarantees exactly-once. N must comfortably exceed the scrape+analyze latency and the max expected gap between runs (e.g. 24-48h) so a late-analyzed announcement is still caught. Confirm a safe N.
4. **Batch size / rate.** At current volume trivial. If a single pass could match many (announcement × user) pairs, consider a per-pass cap + SMTP rate awareness (Gmail ~500/day free / ~2000/day Workspace). Low priority now; note for scale.
5. **Subject/body content.** Exact email subject + which announcement fields to include (company, ticker, title, score, event_type, link to the announcement/app). A product/copy decision for planning; must HTML-escape everything (PR #159).
