---
change_id: email-notifications-delivery
title: Email delivery for watchlist announcement notifications (PUL-81 slice b)
status: implementing
created: 2026-07-21
updated: 2026-07-21
archived_at: null
tracking:
  linear: PUL-81
  github: 140
---

## Notes

build the delivery half of PUL-81 slice b: a runnable pass that finds new announcements for watched companies, joins notification_subscriptions where enabled, sends an email per user via existing SMTP, and dedups via a sent-log table so nobody gets the same announcement twice

**Continuation of slice (a)** (`context/archive/2026-07-21-email-notifications-delivery`… actually slice a = `context/archive/2026-07-21-email-notifications-settings/`). Slice (a) shipped the settings UI + `notification_subscriptions` table + `GET/POST /api/notifications/settings`. This slice consumes that table and actually sends the emails.

**Scope (this change):**
- New BQ `notification_sent_log` table (dedup key `(user_id, announcement_id)`) — shape was deferred from slice (a).
- A runnable delivery pass (entry-point script, like the other pipeline jobs): find announcements newer than the last processed watermark → join watchlists → filter `notification_subscriptions.enabled = true` and `score >= min_score` → send one email per user via existing `src/notifier.py` SMTP → record in sent-log; skip anything already in sent-log.
- Reuse existing SMTP (`_send`) for MVP volume (a few accounts). Branded HTML mail like the verification/reset templates.

**Key decisions to settle in planning:**
- **Delivery infra**: existing Gmail SMTP is fine for the current 2–3 accounts (the user's test). ESP + authenticated own-domain (SES/Postmark/Resend) is a *scaling* decision — defer unless we decide to do it now. See slice-a research "Email deliverability at scale".
- **"New announcement" watermark**: how the pass knows what's new (last-run timestamp vs a processed flag vs sent-log as the only dedup). Company_daily_stats/announcements query patterns matter (ROW_NUMBER pattern per project memory).
- **min_score**: slice (a) stores it (default 0); the cron filters on it. Confirm the UI never set it, so effectively 0 for now.

**Out of scope:**
- Cloud Run job + Cloud Scheduler *creation* on GCP — human-only step at the end (same pattern as `etf-quotes`/`company-stats` triggers). This change delivers the runnable code + tables; the user provisions the scheduler.
- Additional notification channels / subscription gating (future).

**Test hook the user wants:** once the delivery pass is runnable + deployed, add a dummy TOA (Toya) announcement to BQ, run the pass, confirm both opted-in accounts receive the email, then remove the dummy row.
