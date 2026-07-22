---
change_id: event-driven-notifications
title: Event-driven watchlist notifications — send on announcement insert (PUL-81 slice b-v2)
status: archived
created: 2026-07-21
updated: 2026-07-22
archived_at: 2026-07-22T18:28:21Z
tracking:
  linear: PUL-81
  github: 140
---

## Notes

**Pivot from the 5-min cron (slice b) to event-driven delivery.** Slice b shipped a Cloud Run job (`notification_main.py`) polling every 5 min; it's LIVE on prod and works (e2e verified — real emails to both accounts). This change replaces that trigger with an **insert-time hook**: send the email the moment an announcement is ingested + analyzed, so it arrives exactly when it appears on our site. See [[session-2026-07-21b]] for full context.

**Decisions (already made with the user, 2026-07-21):**
- **Trigger**: hook inside `main.py`'s ingestion loop, **right after `save_analysis_result`** (score + approved are known there). Not literally at INSERT — after analysis, per announcement. The scraper runs `*/15`, so the announcement only exists on the site after ingestion anyway; the hook fires in that same run.
- **Link in email**: `?view=my-wallet` (Obserwowane — the user's watchlist hub). NOT ticker-filter (current), NOT Bankier. The email body already carries company + title, so the link is secondary; my-wallet is one fixed link that pulls the user into Faro. (JWT-only view — fine, recipient has an account.)
- **1 email = 1 announcement** (per-announcement, instant) — reuses the digest template rendered with a single item.

**Reuse from slice b (already on master):**
- BQ: `notification_sent_log` table + `record_notification_sent` (idempotent INSERT…WHERE NOT EXISTS). Keep.
- Email: `send_announcement_digest_email` / `_announcement_digest_html` in `src/notifier.py`. Change the per-item link from `{base_url}/?view=announcements&ticker=…` to `{base_url}/?view=my-wallet`.
- Filter logic: enabled + watchlist + `analysis_score >= COALESCE(min_score,0)` + since-opt-in (`published_at >= COALESCE(confirmed_at, updated_at)`) + sent-log anti-join.

**New work:**
- `select_recipients_for_announcement(announcement_id) -> list[dict]` in `db/bigquery.py` — given ONE announcement, return enabled watchers (user_id, email) not yet sent, score/opt-in filtered. (Per-announcement variant of `select_pending_notifications`.)
- Hook in `main.py` after `save_analysis_result`: for the just-analyzed announcement, `select_recipients_for_announcement` → per recipient: `send_announcement_digest_email([this_announcement], base_url)` → `record_notification_sent`. **Per-recipient try/except** (log + continue + owner alert) so a bad email never breaks the scraper/ingest. `APP_BASE_URL` env (default `https://puls-gpw-api-5zlombicra-lm.a.run.app`) — main.py / the scraper job needs it set (add to the scraper job's env in infra.md runbook + deploy).

**Retire the cron (slice b):**
- Remove the `Update Cloud Run Job (notifications)` step from `.github/workflows/deploy.yml`.
- Remove `notification_main.py` (and its test) OR keep as a manual safety-sweep (decide in plan — leaning remove, since the hook covers it and `select_pending_notifications` can stay unused or be dropped).
- Remove the `puls-gpw-notifications` runbook + scheduler row from `infra.md`.
- **HUMAN-ONLY (per CLAUDE.md):** delete the Cloud Scheduler `puls-gpw-notifications-trigger` and the Cloud Run job `puls-gpw-notifications` on GCP (destructive). The cron stays live until v2 ships and replaces it.

**Gotchas (from session):**
- `gcloud` CLI hangs in this env (execute/describe background for 2-3 min but complete exit 0); `bq query` works fine — verify with bq.
- `APP_BASE_URL` = the run.app web URL, NOT `gpw.okiem.ai` (that's SMTP-From only).
- Jobs bootstrap their own tables (no API startup hook). `main.py` already calls `create_*`/`ensure_*` at top — add the notification-table creators there too (idempotent).
- `analysis_score` ~20-160 (not 0-100); NULL never qualifies.

**Open questions for /10x-plan:**
- Does the SMTP send inline in `main.py` add unacceptable latency to the scraper run at current volume? (Few watchers → negligible; note for scale — could move to BackgroundTasks/queue later.)
- Keep `select_pending_notifications` + `notification_main.py` as a safety-sweep, or fully remove? (Leaning remove for a clean single path.)
- Should the hook also handle re-analysis (an announcement re-analyzed with a new score)? sent-log dedup already prevents re-send per pair.
