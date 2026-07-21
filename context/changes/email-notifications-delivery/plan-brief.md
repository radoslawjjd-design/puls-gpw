# Email delivery for watchlist announcement notifications (PUL-81 slice b) — Plan Brief

> Full plan: `context/changes/email-notifications-delivery/plan.md`
> Research: `context/changes/email-notifications-delivery/research.md`

## What & Why

Actually send the emails that slice (a) let users opt into. A new Cloud Run job (~every 5 min, 24/7) finds new analyzed announcements for watched companies, and emails each opted-in user a single digest of their new announcements. This is what unblocks the end-to-end "add an announcement → get an email" test.

## Starting Point

Slice (a) shipped the `notification_subscriptions` table (with `enabled`, `email`, `min_score`) and the settings UI, but nothing consumes it — no delivery pipeline exists. The app already scrapes + scores announcements (Bankier.pl source, `analysis_score` on the announcement row) and has a reusable Gmail SMTP layer (`src/notifier`) and a well-worn Cloud Run job pattern (`etf_quotes_main.py`).

## Desired End State

A deployed `puls-gpw-notifications` job runs every ~5 min: for each `enabled` user it selects their watched tickers' new, approved, `score >= min_score` announcements not yet sent, emails a Faro-branded digest (company (ticker) — title — event type, linking to the Faro announcements view filtered by ticker), and records each `(user, announcement)` in a new `notification_sent_log` so re-runs never double-send. A bad recipient is skipped + retried next pass + reported to the owner, never blocking others.

## Key Decisions Made

| Decision | Choice | Why | Source |
| --- | --- | --- | --- |
| Delivery infra | Existing Gmail SMTP | Enough for current 2-3 accounts; ESP is a future scaling decision | Plan |
| Schedule | Every ~5 min, 24/7 | ESPI announcements arrive off-hours/weekends; notifications are time-sensitive | Plan |
| Granularity | Digest per user per pass | Fewer emails when several announcements land at once | Plan |
| Send failure | Continue + owner alert | One bad address must not block others; no sent-log record → auto-retry | Plan |
| Email link | Faro list filtered by ticker | Drives users into the app (no per-announcement detail page exists) | Plan |
| Email content | Company(ticker) + title + event_type | Readable; `analysis_score` is an internal ~20-160 metric, misleading to users | Plan/Research |
| Idempotency | send-then-record + `INSERT WHERE NOT EXISTS` | Rare duplicate beats a silent miss; record write is self-idempotent | Research |

## Scope

**In scope:** `notification_sent_log` table + `select_pending_notifications` (the join) + `record_notification_sent`; a branded digest email in `notifier.py`; `notification_main.py` job; the `deploy.yml` `jobs update` step; the `infra.md` runbook entry; unit tests.

**Out of scope:** ESP/own-domain email infra; automated GCP job/scheduler creation (human step); `analysis_score` / summary in the email; per-announcement detail page; `min_score` UI; extra channels.

## Architecture / Approach

`notification_main.py` (Cloud Run job, mirrors `etf_quotes_main.py`) → bootstraps its own tables → `select_pending_notifications(now-48h)` (announcements × watchlist × subscriptions, anti-joined against the sent-log, partition-pruned) → group rows by user → send one digest per user via Gmail SMTP → record each pair. `APP_BASE_URL` env supplies the link/logo origin (no request context in a cron).

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. BQ data layer | sent-log table + select-pending join + record fn, unit-tested | join/filter correctness (mitigated by existing templates) |
| 2. Digest email | branded `send_announcement_digest_email` + escaping, unit-tested | HTML-escaping every field incl. ticker-in-URL (PR #159) |
| 3. Delivery job | `notification_main.py` + deploy step + runbook, unit-tested | idempotency ordering; the GCP create is a human step |

**Prerequisites:** slice (a) deployed (done). No new GCP client, no new libraries.
**Estimated effort:** ~1 focused session across 3 phases + a human GCP-provisioning + end-to-end test step.

## Open Risks & Assumptions

- Assumes Gmail SMTP suffices at current volume; a durably-bad address alerts the owner every pass until fixed by hand.
- Assumes `APP_BASE_URL=https://gpw.okiem.ai` is the right public origin for the Faro link/logo — confirm the custom domain at runbook time.
- The Cloud Run job + scheduler are created by a human (CLAUDE.md: infra creation is human-only); the job stays idle until then.
- 48h candidate window assumes scrape+analyze latency stays well under 48h; the sent-log guarantees exactly-once regardless.

## Success Criteria (Summary)

- Opted-in users receive a digest email for new qualifying announcements of their watched companies; re-runs never duplicate.
- A per-recipient failure is retried next pass and reported to the owner; it never blocks other recipients.
- Unit tests (BQ + notifier + job loop) pass; the dummy-TOA end-to-end test delivers to both opted-in accounts.
