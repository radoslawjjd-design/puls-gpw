---
date: 2026-10-29
researcher: Codex
git_commit: 5a143f5
branch: migration/gcp-kacha-20261029
repository: puls-gpw
topic: Full migration to a new Google Cloud project and administrator account
tags: [research, migration, gcp, firebase, bigquery]
status: complete
last_updated: 2026-10-29
last_updated_by: Codex
---

# Research: GCP account migration

## Scope and live inventory

Source `puls-gpw` (139573454149); destination `puls-gpw-kp-20261029`
(530001799240); region `europe-central2`. Source metadata was captured on
2026-10-29 under `.cache/gcp-account-migration-kacha/source-20261029/`.
That directory is excluded from Git and Docker. No secret values were exported.

Live source: five jobs, seven enabled schedulers, fifteen secrets, fifteen
BigQuery tables in `espi_ebi`, about 2.08 million rows. One serving API
(`puls-gpw-api`); a second service `puls-gpw-app` has been failed/non-serving
since June and is retained in the source rather than recreated. Two storage
buckets hold build/source artifacts, not application database state.

## Portability findings

- `db/bigquery.py:100`: database project follows runtime configuration and ADC
  quota project. Preserve live table schemas, partitioning, clustering and IDs.
- `db/bigquery.py:4400`: billing export table is hardcoded to the source billing
  account. Make it configurable; a copied historical table is not a live export.
- `src/gemini_client.py:29`: runtime Gemini uses Vertex with service credentials,
  project from the environment and region `global`; the Gemini API key binding
  is legacy.
- `src/auth.py:286,313,330`: Firebase certificate and Web API key select the auth
  project; Firebase localId becomes the BQ user_id. Preserve UIDs, password
  hashes/salts with source SCRYPT settings, emailVerified and disabled state.
  Source hash settings and password hashes must only pass through memory.
- `src/notifier.py:19`: SMTP identity/owner email are separate from Cloud account
  ownership. Preserve current mail identity and alert destination.
- `main.py:41`, `cost_report_main.py:43`: set APP_BASE_URL to target service URL.
- `.github/workflows/deploy.yml:21,23,124`: project/image/runner are hardcoded.
  Deployment and both AI-review workflows share source credentials; switch all
  coherently and retain source credentials for rollback.
- `.github/workflows/deploy.yml:74`: currently forces X_AUTO_PUBLISH=true.
  Destination staging must stay unpublished and unscheduled until final cutover.

## Cutover integrity

Pause schedules AND block source public API writes/access, then drain in-flight
jobs and API requests before the final Firebase and BigQuery synchronization.
The API writes user/watchlist/portfolio/subscription records and has no existing
maintenance flag. Announcement/email/X dedup is not a cross-project lock.
Preserve announcement IDs, notification sent-log, post publication/tweet IDs,
all broker operations and portfolio ownership. Compare counts/key multisets and
schemas after the freeze, not while writes continue.

Never run main.py or post_main.py as smoke tests: they send mail/publish. Use
read-only API/data checks and synthetic Vertex/Bankier probes. Existing Bankier
Cloudflare blocking may persist on the target.

Before destination accepts writes, rollback is restoring source access and
schedules. After destination accepts writes, reverse synchronization is required
before returning to source; simply resuming source could lose data or duplicate
notifications.

## Historical references and current documentation

- `C:/oswiadczenia_gwp/scripts/migration/POSTMORTEM_2026-05-14.md`: prior migration
  lessons, especially project-number rewrites, environment variables and BQ
  partitioning. Historical scripts are not directly reusable for this app.
- https://firebase.google.com/docs/auth/admin/import-users
- https://firebase.google.com/docs/cli/auth
- https://docs.cloud.google.com/bigquery/docs/managing-tables
- https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-setup

Regional billing exports begin at enablement and may lag by hours. Preserve the
source export as historical data; do not write old records into Google's new
export table. Cost-report scheduler remains paused until target billing data is
available. Existing reporting is account-wide, which is preserved in migration.

## Verification still required

Target Firebase activation/settings, identity import, copied data, serving API,
images, runtime authorization, CI identity, monitoring and target billing export.
