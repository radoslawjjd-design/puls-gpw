# Event-driven watchlist notifications (PUL-81 slice b-v2) Implementation Plan

## Overview

Replace the 5-min polling cron (slice b) with an **insert-time hook**: `main.py`'s ingestion loop emails opted-in watchers the moment an announcement is stored + analyzed, so the email arrives exactly when the announcement appears on our site. One email per announcement; the link points at the user's watchlist (`?view=my-wallet`). Reuses the sent-log, record function, and email template from slice b; retires the standalone cron job + scheduler.

## Current State Analysis

- Slice b shipped and is LIVE: a Cloud Run job `notification_main.py` + Cloud Scheduler `puls-gpw-notifications-trigger` (`*/5`) polls `select_pending_notifications(now-48h)` and emails digests. It works (e2e verified). This change supersedes it.
- The ESPI/EBI ingestion pipeline is `main.py` (Cloud Run job `puls-gpw`, scheduler `*/15`). Its per-item loop (`main.py:55-101`): parse → `insert_announcement` → `update_parsed_content` → `upsert_company` (best-effort, `try/except BigQueryError` isolated) → `analyze_announcement` → `save_analysis_result` (best-effort, `try/except BigQueryError`). `BigQueryError` in the core steps re-raises to the outer handler (alert + `sys.exit(1)`); other per-item exceptions are logged and the loop continues. `main.py` bootstraps its own tables at the top (`:44-48`).
- After `save_analysis_result` (`main.py:81-88`), the announcement's `analysis_approved`, `analysis_score`, `event_type`, plus `ann_id`, `parsed.ticker`, `parsed.company`, `ann.title`, `ann.published_at` are all in hand — the exact fields a notification needs, no re-read required.
- Reusable (on master from slice b): `notification_sent_log` table + `create_notification_sent_log_table_if_not_exists`/`ensure`; `record_notification_sent` (idempotent INSERT…WHERE NOT EXISTS); `send_announcement_digest_email`/`_announcement_digest_html` in `src/notifier.py`; `_event_type_label`. The `notification_subscriptions` table (`enabled`, `email`, `min_score`, `confirmed_at`) and `watchlist` (keyed on `user_id`).
- `select_pending_notifications` (window scan) is used ONLY by `notification_main.py` — both become dead once the hook lands.
- The scraper job `puls-gpw` already has SMTP secrets (it calls `send_alert`), but does NOT have `APP_BASE_URL` set — the hook's email link/logo need it.

Full grounding: `context/changes/email-notifications-delivery/research.md` (slice b) + `change.md` decisions.

## Desired End State

When the `puls-gpw` scraper runs (`*/15`), each newly-ingested, approved, scored announcement of a watched company triggers — inline, right after its analysis is saved — one Faro-branded email to each opted-in watcher (enabled, `score >= min_score`, published after their opt-in, not already sent), linking to `?view=my-wallet`. Delivery is deduplicated via `notification_sent_log`. A notification failure never affects ingestion (isolated, logged, owner-alerted). The standalone `notification_main.py` job, its scheduler, `select_pending_notifications`, and the deploy/infra wiring are gone. Verified by: unit tests (new BQ query, the hook path, the email link) + a live check (new real announcement or dummy → watchers emailed once, arriving with site publication).

### Key Discoveries:

- Hook insertion point: `main.py` immediately after the `save_analysis_result` try-block (`:88`/`:96`), inside the per-item loop, in its OWN `try/except` (mirror `upsert_company` isolation `:73-78`) so a send failure never breaks ingestion. Gate on `result.analysis_approved is True and result.analysis_score is not None`.
- New BQ fn mirrors `select_pending_notifications` (slice b, `db/bigquery.py`) but scoped to one `announcement_id` (drops the `@candidate_cutoff` window; keeps enabled/email/score/min_score/since-opt-in/anti-join).
- Email fields come from `main.py` locals — the new query returns only `{user_id, email}`.
- Email link: `_announcement_digest_html` currently builds `{base_url}/?view=announcements&ticker={ticker}` — change to a single `{base_url}/?view=my-wallet` (no ticker param).
- `main.py` must add `create_notification_sent_log_table_if_not_exists()` + `ensure_notification_sent_log_schema_current()` to its startup block (`:44-48`) — jobs bootstrap their own tables.
- `APP_BASE_URL` default = `https://puls-gpw-api-5zlombicra-lm.a.run.app` (the run.app web URL, NOT `gpw.okiem.ai`).

## What We're NOT Doing

- No digest/batching across a run — one email per announcement per watcher (per the decision).
- No async queue / BackgroundTasks — the send is inline in the scraper loop (acceptable at current volume; noted for scale).
- No new email content/fields — same template, only the link changes.
- No ESP / own-domain migration (future).
- No change to `min_score` (stays 0-default), the double-opt-in stance, or the score semantics.
- No keeping `notification_main.py` / `select_pending_notifications` as a safety-sweep — removed entirely (decision).

## Implementation Approach

Additive-first, then retire: (1) add the per-announcement recipient query alongside the existing window query; (2) wire the isolated hook into `main.py` + flip the email link; (3) once the event-driven path is proven, delete the cron code, its deploy/infra wiring, and (on GCP) the job + scheduler, and add `APP_BASE_URL` to the scraper job. This ordering never leaves the tree with a deleted function still imported.

## Critical Implementation Details

- **Isolation is load-bearing.** The notification hook must be wrapped so NO failure (SMTP, BigQueryError from the recipient query or record, anything) propagates out of the per-item loop — otherwise a mail hiccup would abort the ingestion run (`sys.exit(1)`) and lose scraping. Catch broadly inside the hook, log, set a run-level `notif_failures` counter, and `send_alert` once at the end if non-zero (never `sys.exit`). This is stricter than `main.py`'s core steps, where `BigQueryError` intentionally propagates.
- **Send-then-record, per recipient — no next-pass retry (F1).** For each recipient: send the email, then `record_notification_sent(user_id, ann_id, email)`. **Unlike slice b's cron, there is NO next pass**: the scraper skips already-processed announcements (`get_processed_ids_since` dedups by `announcement_id`), so a given announcement is analyzed + hooked exactly once. A send that fails is therefore **permanently missed** — nothing re-attempts it. Mitigation: retry the send in-run (2–3 attempts, short backoff) before giving up; a sustained SMTP outage still misses those emails, which is accepted (notifications are low-stakes). Do NOT rely on any "retried next pass" behavior. The `WHERE NOT EXISTS` still keeps the log idempotent (a crash between a successful send and its record re-sends only if that exact announcement were ever re-processed, which normally it isn't).
- **Removal ordering.** `select_pending_notifications` may only be deleted in the same phase as its sole caller `notification_main.py` (Phase 3) — never earlier, or the tree won't import.

## Phase 1: Per-announcement recipient query

### Overview

Add the BQ function that, given one announcement id, returns the opted-in watchers who should be emailed and haven't been. Unit-test it.

### Changes Required:

#### 1. `select_recipients_for_announcement`

**File**: `db/bigquery.py`

**Intent**: Per-announcement variant of `select_pending_notifications` — scope the same join/filters to a single `announcement_id`, returning just the recipients.

**Contract**: `select_recipients_for_announcement(announcement_id: str) -> list[dict]` — join `announcements a` (WHERE `a.announcement_id = @announcement_id AND a.analysis_approved = TRUE AND a.analysis_score IS NOT NULL`) × `watchlist w ON w.ticker = a.ticker` × `notification_subscriptions ns ON ns.user_id = w.user_id`, filtered `ns.enabled = TRUE AND ns.email IS NOT NULL AND a.analysis_score >= COALESCE(ns.min_score, 0) AND a.published_at >= COALESCE(ns.confirmed_at, ns.updated_at) AND NOT EXISTS (sent-log for that user+announcement)`. Returns `[{user_id, email}]` (ordered by `user_id`). `BigQueryError`-wrapped. Template: `select_pending_notifications` (same file) minus the `@candidate_cutoff` window.

#### 2. Unit tests

**File**: `tests/test_bigquery.py`

**Intent**: Verify the query shape (join, filters, single-id param, anti-join) and row mapping, plus empty→[] and error wrapping.

**Contract**: `patch("db.bigquery._get_client", …)` with `_mock_bq_client_with_rows(...)`; assert the query binds `@announcement_id`, contains `enabled = TRUE`, `analysis_approved = TRUE`, `COALESCE(ns.min_score, 0)`, `confirmed_at`, `NOT EXISTS … notification_sent_log`; row maps to `{user_id, email}`; empty→[]; `BigQueryError` on client failure.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_bigquery.py -q`
- Full suite green: `uv run pytest --tb=short`

#### Manual Verification:

- `select_recipients_for_announcement` with an empty mocked result returns `[]` without raising.

**Implementation Note**: After automated verification passes, pause for human confirmation before Phase 2.

---

## Phase 2: Event-driven send in `main.py` + email link

### Overview

Wire the isolated notification hook into the ingestion loop and flip the email link to the watchlist view. Unit-test the hook path and the link.

### Changes Required:

#### 1. Email link → watchlist

**File**: `src/notifier.py`

**Intent**: Each digest entry (now always one announcement) links to the user's watchlist hub instead of a ticker-filtered announcements list.

**Contract**: In `_announcement_digest_html`, replace the per-item link `f"{base_url}/?view=announcements&ticker={…}"` with `f"{base_url}/?view=my-wallet"` (single fixed link, no ticker param; still `_html_escape`d). Update the existing notifier test that asserts the ticker link.

#### 2. Notification hook in the ingestion loop

**File**: `main.py`

**Intent**: After an announcement is analyzed + saved (approved, scored), email each opted-in watcher — fully isolated from ingestion.

**Contract**: Import `create_notification_sent_log_table_if_not_exists`, `ensure_notification_sent_log_schema_current`, `select_recipients_for_announcement`, `record_notification_sent`, `send_announcement_digest_email`. Add the two sent-log table creators to the startup block (`:44-48`). Read `base_url = os.environ.get("APP_BASE_URL", "https://puls-gpw-api-5zlombicra-lm.a.run.app")` once. After the `save_analysis_result` try-block, when `result.analysis_approved is True and result.analysis_score is not None`, run a `try/except`-wrapped hook: `recipients = select_recipients_for_announcement(ann_id)`; build the item dict `{company, ticker, title, event_type}` from locals; for each recipient: send with a small **in-run retry** (2–3 attempts, short backoff — e.g. `send_announcement_digest_email(email, [item], base_url)` retried on `Exception`), then on success `record_notification_sent(user_id, ann_id, email)`; if all attempts fail, log + increment a `notif_failures` counter + continue (the send is permanently missed — there is no next pass, F1); the whole hook is additionally wrapped so NOTHING (incl. `BigQueryError` from the recipient query) escapes to abort ingestion. After the loop, if `notif_failures`, `send_alert(...)` once (do not `sys.exit`).

#### 3. Unit tests

**File**: `tests/test_main.py`

**Intent**: Verify the hook fires for approved+scored announcements, is skipped otherwise, records each recipient, and never breaks the run on failure.

**Contract**: Extend `test_main.py`'s collaborator-monkeypatch fixture with `select_recipients_for_announcement`, `send_announcement_digest_email`, `record_notification_sent`, and the sent-log table creators (in the `main` module namespace). Tests: (a) approved+scored announcement with 2 recipients → 2 sends + 2 records; (b) rejected/`None`-score announcement → hook not called; (c) a recipient send raising → other recipients still processed, run still completes (no `SystemExit`), `send_alert` fired; (d) `select_recipients_for_announcement` raising `BigQueryError` → ingestion still completes (isolated), not fatal.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_main.py tests/test_notifier.py -q`
- Full suite green: `uv run pytest --tb=short`

#### Manual Verification:

- Render/inspect the digest HTML: link is `{base_url}/?view=my-wallet`.
- Locally (or after deploy) a new approved announcement for a watched company emails the watcher once, arriving with ingestion; a second run sends nothing (dedup).

**Implementation Note**: After automated verification passes, pause for human confirmation before Phase 3.

---

## Phase 3: Retire the cron (code + infra + GCP)

### Overview

Delete the now-dead cron path and its wiring, and retire the GCP resources.

### Changes Required:

#### 1. Remove cron code

**File**: `notification_main.py`, `tests/test_notification_main.py`, `db/bigquery.py`, `tests/test_bigquery.py`

**Intent**: Delete the standalone job, its test, and the now-unused window query + its tests.

**Contract**: Delete `notification_main.py` and `tests/test_notification_main.py`. Remove `select_pending_notifications` from `db/bigquery.py` and its tests from `tests/test_bigquery.py`. Confirm no remaining importers (`grep`).

#### 2. Remove deploy + infra wiring

**File**: `.github/workflows/deploy.yml`, `context/foundation/infra.md`

**Intent**: Drop the notifications job from CI and the runbook/scheduler table.

**Contract**: Remove the `Update Cloud Run Job (notifications)` step from `deploy.yml`. In `infra.md`, remove the `puls-gpw-notifications-trigger` scheduler row and the "One-time provisioning runbook — puls-gpw-notifications" section; add a note that the scraper job `puls-gpw` now carries `APP_BASE_URL` (for the notification hook).

#### 3. GCP retirement (human-authorized, executed via gcloud this session)

**File**: (no repo file — prod infra)

**Intent**: Delete the standalone job + scheduler and give the scraper job the base-URL env.

**Contract**: `gcloud scheduler jobs delete puls-gpw-notifications-trigger` + `gcloud run jobs delete puls-gpw-notifications` (destructive — authorized), and `gcloud run jobs update puls-gpw --update-env-vars="APP_BASE_URL=https://puls-gpw-api-5zlombicra-lm.a.run.app"`. Note: `gcloud` CLI hangs in this env but completes — verify with `bq`/list where possible.

### Success Criteria:

#### Automated Verification:

- No dangling references: `grep -rn "select_pending_notifications\|notification_main" --include=*.py .` returns nothing (except in archived context).
- Full suite green: `uv run pytest --tb=short`

#### Manual Verification:

- CI deploy (merge to master) succeeds with the notifications step gone.
- GCP: `puls-gpw-notifications` job + `puls-gpw-notifications-trigger` scheduler are gone; `puls-gpw` job has `APP_BASE_URL` set.
- End-to-end: a fresh approved announcement (real or dummy TOA) for a watched company emails both opted-in accounts once, with the `?view=my-wallet` link; re-run/next scrape sends nothing. Remove any dummy row.

**Implementation Note**: After automated verification passes, pause for human confirmation. Final phase; the GCP deletes + e2e are done in-session (authorized).

---

## Testing Strategy

### Unit Tests:

- `db.bigquery` — `select_recipients_for_announcement` (join/filters/single-id param, mapping, empty, error).
- `src.notifier` — the digest link is `?view=my-wallet`.
- `main` — hook fires for approved+scored, skipped otherwise, records recipients, isolated on failure (no `SystemExit`).

### Integration / Manual Tests:

- Dummy-TOA (or real) end-to-end after deploy: emailed once with the my-wallet link; dedup on re-scrape.

### Manual Testing Steps:

1. After merge+deploy, insert a dummy approved TOA announcement (recent `published_at`, `analyzed_at`, `analysis_approved=TRUE`, score 100) OR wait for a real one.
2. Trigger the scraper (or wait for `*/15`); confirm both opted-in accounts get one email with `?view=my-wallet`.
3. Re-run; confirm zero new sends (sent-log). Remove any dummy row.

## Performance Considerations

The send is inline in the scraper loop: SMTP latency (~1-2 s/recipient, 10 s timeout) is added per approved announcement × watcher. Negligible at current volume (2 subscribers). At scale this belongs in a queue/BackgroundTasks — noted, out of scope now. The per-announcement query is a small indexed join.

## Migration Notes

`notification_sent_log` already exists (slice b) and self-provisions via `main.py`'s startup block. Removing `select_pending_notifications` + `notification_main.py` is pure deletion (no data change). The `puls-gpw` scraper job gains `APP_BASE_URL`. GCP job/scheduler deletes are reversible via the (removed) runbook if ever needed — kept in git history / the archived slice-b change.

## References

- Slice b (cron) change: `context/changes/email-notifications-delivery/` (research + plan + code on master)
- Session context + decisions: `change.md`, `[[session-2026-07-21b]]`
- Hook point: `main.py:55-101`; reusable BQ/email: `db/bigquery.py` (sent-log/record), `src/notifier.py` (`send_announcement_digest_email`)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Per-announcement recipient query

#### Automated

- [x] 1.1 Unit tests pass: `uv run pytest tests/test_bigquery.py -q` — ebc3154
- [x] 1.2 Full suite green: `uv run pytest --tb=short` — ebc3154

#### Manual

- [x] 1.3 `select_recipients_for_announcement` with empty mocked result returns `[]` without raising — ebc3154

### Phase 2: Event-driven send in main.py + email link

#### Automated

- [x] 2.1 Unit tests pass: `uv run pytest tests/test_main.py tests/test_notifier.py -q` — 3329557
- [x] 2.2 Full suite green: `uv run pytest --tb=short` — 3329557

#### Manual

- [ ] 2.3 Digest link is `{base_url}/?view=my-wallet`; a new approved announcement emails the watcher once and dedups on re-run

### Phase 3: Retire the cron (code + infra + GCP)

#### Automated

- [ ] 3.1 No dangling references to `select_pending_notifications` / `notification_main` (grep clean)
- [ ] 3.2 Full suite green: `uv run pytest --tb=short`

#### Manual

- [ ] 3.3 CI deploy succeeds with the notifications step removed
- [ ] 3.4 GCP: notifications job + scheduler deleted; `puls-gpw` job has `APP_BASE_URL`
- [ ] 3.5 End-to-end: approved announcement → both opted-in accounts emailed once with `?view=my-wallet` → re-scrape sends nothing → dummy removed
