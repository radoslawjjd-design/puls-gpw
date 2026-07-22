# Event-driven watchlist notifications (PUL-81 slice b-v2) — Plan Brief

> Full plan: `context/changes/event-driven-notifications/plan.md`
> Context/decisions: `context/changes/event-driven-notifications/change.md`

## What & Why

Replace the 5-min polling cron (slice b) with an insert-time hook so notification emails arrive **exactly when an announcement appears on our site**, not up to 5 min later — and remove the standalone job/scheduler. The email links to the user's watchlist (`?view=my-wallet`).

## Starting Point

Slice b is LIVE: a Cloud Run job (`notification_main.py`) + `*/5` scheduler polls `select_pending_notifications` and emails digests (works, e2e verified). The ingestion pipeline `main.py` (job `puls-gpw`, `*/15`) already stores + analyzes announcements per item; right after `save_analysis_result` all the fields a notification needs are in hand. The sent-log table, `record_notification_sent`, and the branded email are reusable.

## Desired End State

When the scraper ingests + analyzes a new approved, scored announcement of a watched company, `main.py` emails each opted-in watcher once — inline, arriving with site publication — deduplicated by the sent-log, fully isolated from ingestion (a mail failure never aborts a scrape). The cron job, scheduler, `select_pending_notifications`, and the deploy/infra wiring are gone.

## Key Decisions Made

| Decision | Choice | Why | Source |
| --- | --- | --- | --- |
| Trigger | Insert-time hook in `main.py` (after `save_analysis_result`) | Email arrives with site publication; drops the polling cron | change.md |
| Email link | `?view=my-wallet` | Body already carries company+title; one fixed link pulls user into Faro | change.md |
| Granularity | 1 email = 1 announcement | Instant per-announcement (reuses digest template with one item) | change.md |
| Cron code | Remove entirely | Single clean path; hook covers it | Plan |
| GCP retirement | Done in-session (authorized) | delete job+scheduler, add `APP_BASE_URL` to `puls-gpw` | Plan |
| Isolation | Hook catches everything; never `sys.exit` | A mail failure must not abort ingestion | Plan |

## Scope

**In scope:** `select_recipients_for_announcement(announcement_id)` (per-announcement query); the isolated hook in `main.py` + sent-log bootstrap; email link → my-wallet; deletion of `notification_main.py`/`select_pending_notifications`/deploy step/runbook; GCP job+scheduler delete + `APP_BASE_URL` on the scraper job.

**Out of scope:** digest/batching, async queue/BackgroundTasks, email content changes, ESP migration, `min_score`/score-semantics changes, keeping a safety-sweep.

## Architecture / Approach

Additive-then-retire: (1) add the per-announcement recipient query; (2) wire the isolated hook into `main.py`'s loop after analysis + flip the email link; (3) delete the cron code/infra and the GCP resources. Removing `select_pending_notifications` happens in the same phase as removing its only caller (`notification_main.py`) so the tree never imports a deleted symbol.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. BQ query | `select_recipients_for_announcement`, unit-tested | filter parity with the window query |
| 2. main.py hook | Inline event-driven send + my-wallet link, tested | isolation — a mail failure must not abort the scrape |
| 3. Retire cron | Delete code/infra + GCP job/scheduler, add `APP_BASE_URL` | ordering (delete function with its only caller); destructive GCP |

**Prerequisites:** slice b on master (done); SMTP creds already on the `puls-gpw` job. **Estimated effort:** ~1 session across 3 phases + in-session GCP + e2e.

## Open Risks & Assumptions

- Inline SMTP adds latency to the scraper run — negligible at 2 subscribers; a queue is the scale answer (out of scope).
- The cron stays live until Phase 3 retires it — no gap in coverage during the migration.
- Assumes `APP_BASE_URL` = the run.app web URL (`puls-gpw-api-5zlombicra-lm.a.run.app`), not `gpw.okiem.ai` (SMTP-From only).

## Success Criteria (Summary)

- A new approved announcement for a watched company emails opted-in watchers once, arriving with site publication, linking to `?view=my-wallet`; re-scrape never double-sends.
- A notification failure is logged + owner-alerted but never aborts an ingestion run.
- The cron job, scheduler, and dead code are gone; the full suite stays green.
