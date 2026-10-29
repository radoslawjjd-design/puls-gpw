# Migration execution record

Source: `puls-gpw`; destination: `puls-gpw-kp-20261029`; region: `europe-central2`.
Account identities and billing IDs are in the local ignored scope file.

## Progress

- [x] Target administrator authenticated without switching default CLI account.
- [x] Dedicated migration branch and change record created.
- [x] Source inventory captured without secrets/row data; temporary query datasets excluded.
- [x] Destination project created and linked to the target's existing billing account.
- [x] Required initial destination APIs enabled.
- [x] Auth/data and deployment research completed by two read-only agents.
- [x] Target service accounts, IAM, repository, dataset and Firebase created.
- [x] Secrets transferred in memory; target Firebase credentials configured.
- [x] Initial BigQuery and Firebase copies verified (15 tables, 7 accounts).
- [x] Images/jobs/private API deployed; target schedules remain inactive.
- [x] Portable billing/CI configuration implemented; 996 tests pass, 9 skipped.
- [x] Read-only API/auth/BQ probes passed; two supported Vertex models passed.
- [x] Bankier probe completed: HTTP 403 / Cloudflare challenge persists.
- [x] Source writes frozen and in-flight work drained.
- [x] Final data/identity synchronization and exact row-multiset comparisons pass.
- [x] Public access and five healthy schedules switched to destination.
- [x] CI identity switched and smoke-tested; billing export configured.
- [x] Monitoring copied but absence-of-scraper-executions alert intentionally disabled.
- [x] Temporary source-account grants to target BigQuery removed.
- [x] First full deployment through destination CI/CD verified (run 40841274670).
- [ ] Bankier access repaired; scraper schedule and monitoring resumed.
- [ ] Billing export contains yesterday's data; cost report schedule resumed.

## Constraints

Private API probes passed: health, frontend, admin authorization, announcement
query, ticker autocomplete. Target Firebase credential reads all 7 preserved UIDs.
Cloud Run BQ probe passed. Bankier still returns 403 with a Cloudflare challenge.
Legacy Gemini 2.5 model returned 404 in the new project. Runtime configuration
now selects gemini-3.1-flash-lite and gemini-3.5-flash, both verified with JSON
responses from the actual target Cloud Run service account. CI reviews use
gemini-3.5-flash. Target billing export enabled by the user; the table exists
but was empty at the cutover check.

## Serving configuration

- Application: https://puls-gpw-api-530001799240.europe-central2.run.app
- Console: https://console.cloud.google.com/run?project=puls-gpw-kp-20261029
- Production dataset: `puls-gpw-kp-20261029.espi_ebi`.
- Preview dataset: `puls-gpw-kp-20261029.espi_ebi_preview` (retained, not serving).
- Source API ingress: internal-only; all seven source schedules paused.
- Target schedules enabled: company-stats, ETF quotes and the three post windows.
- Target schedules paused: `puls-gpw-trigger` (403) and
  `puls-gpw-cost-report-trigger` (no billing rows yet).
- Old execution-absence alert disabled after cutover. Its target copy remains
  disabled until the scraper can run; SMTP application alerts remain configured.
- Source resources retained. They have not been deleted, nor has source billing
  been disconnected. Retained storage can still incur charges.
- SMTP sender/recipient and X account are unchanged. The Google Cloud administrator
  changed; this is not a change of the application's owner-email destination.

## Verified data

| Table | Rows |
| --- | ---: |
| companies | 747 |
| etf_instruments | 57 |
| announcements | 8,505 |
| company_daily_stats | 1,946,808 |
| notification_sent_log | 71 |
| etf_quotes | 26,676 |
| historical billing export | 92,691 |
| notification_subscriptions | 2 |
| portfolio_snapshots | 14 |
| user_broker_operations | 1,584 |
| user_portfolios | 10 |
| users | 7 |
| watchlist | 16 |
| user_portfolio_positions | 56 |
| x_posts | 293 |

Total: 2,077,537 rows. Verification compared complete row multisets, including
duplicate multiplicities, plus schemas, partitioning and clustering. Application
tables were copied with query jobs so streaming-buffer rows would not be lost.
The ingestion-partitioned billing history used a fixed-time table-copy snapshot,
then comparison against the same `FOR SYSTEM_TIME AS OF` timestamp. This avoids
comparing to a billing export that Google continues updating after app freeze.

Firebase import used the source SCRYPT parameters in memory. It preserved all
7 UIDs, password hashes/salts, email-verification state and account flags. Password
and phone providers are represented by top-level credentials during import;
they must not be copied as federated providers. Once the target accepts writes,
the import helper refuses to overwrite users from the frozen source.

## CI/CD

PR #263 passed unit/integration/E2E CI and both AI reviews, then was merged.
Manual review run 40838855354 passed using target Workload Identity Federation.
The first full destination deployment, run 40841274670, succeeded at
2026-10-29 08:23 UTC: tests, image build/push, five job updates and API deployment.

Repository variables: `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WIF_PROVIDER`,
`GCP_CI_SERVICE_ACCOUNT`, `GEMINI_REVIEW_MODEL`, `X_AUTO_PUBLISH`, `DEPLOY_PAUSED`.
The original `PULS_GPW_SECRET` is retained as a rollback fallback; WIF suppresses
its use when a provider is configured. `.dockerignore` excludes both operational
`.cache/` files and temporary `gha-creds-*.json` files created by GitHub auth.

## Repeat-migration checklist

These scripts are scoped to this particular migration and deliberately reject
different source/target projects. Review and update the guards for another move;
do not blindly rerun bootstrap/copy/freeze against an already serving destination.

1. Create a fresh change folder and branch; inventory jobs, serving services,
   schedulers, IAM, secret references, repositories, monitoring, BQ and Firebase.
   Exclude obsolete failed services and anonymous BQ query-result datasets.
2. Authenticate the new administrator without relying on CLI default account.
   Create a distinct project, attach its billing account and enable required APIs.
3. Create runtime/build/CI/scheduler/Firebase service accounts, least-required
   grants, the image repository and the production dataset in the source region.
4. Add Firebase to the existing GCP project and initialize Authentication through
   its console when Terms/initialization require it. Enable Email/Password only.
   Generate destination-specific Firebase credentials and web API key. Never use
   a copied source Firebase key with destination BQ data.
5. Copy other secrets in memory through Secret Manager. Preserve SMTP and X
   destinations unless the user separately requests changes.
6. Copy BQ to a separate preview dataset. Import Firebase users with source hash
   parameters and preserve their UIDs, which identify owners of BQ records.
7. Build only tracked application files. Stage jobs without executing them and
   deploy an IAM-private API against preview data. Set X publishing false during
   staging. Probe API, BQ, Firebase, current Vertex models and external feeds.
8. Prepare paused schedulers and disabled monitoring. Configure CI project,
   runner identity, image registry and review model. Test WIF, preserving the old
   credentials for rollback. Keep deployment paused through the cutover.
9. Enable standard billing export to the production dataset in the new billing
   console. Set `GCP_BILLING_EXPORT_TABLE` to that account's exact table name.
   Keep old exports as separate historical tables; never append into Google's
   new export table. Update model-cost classification if model versions changed.
10. Freeze source schedulers and external API access. Drain Cloud Run executions
    and at least the API request timeout. Copy final data to empty production
    tables, preserving schema/layout and streaming rows; compare full multisets.
    Re-sync Firebase while the target is still private.
11. Point target jobs/API at production data, restore intended publishing settings,
    verify privately, then open the target API and enable healthy schedules only.
    Never allow the two environments to write/send concurrently.
12. Run a full deployment through CI, check the public application and schedules,
    remove temporary cross-project grants, and record outstanding dependencies.
    Source deletion is a separate human action after the retention decision.

## Local operational files and commands

Ignored state lives in `.cache/gcp-account-migration-kacha/`: `scope.json`,
sanitized inventory, build manifests, cutover state, probe results and audit.
`scope.json` contains source/target project IDs and numbers, source/target account
emails, target billing-account ID and region. It contains no passwords or keys.
These values are needed by `inventory.py`, `cloud.py`, `deploy.py`, `verify.py`
and `cutover.py`. Credentials come from explicitly selected gcloud accounts.

Read-only checks after cutover:

```powershell
.\.venv\Scripts\python.exe context/changes/gcp-account-migration-kacha/verify.py api
.\.venv\Scripts\python.exe context/changes/gcp-account-migration-kacha/verify.py public
.\.venv\Scripts\python.exe context/changes/gcp-account-migration-kacha/verify.py audit
```

The local default gcloud/ADC account was not switched. For future local app runs,
use the new administrator's ADC and update local environment project, Firebase
credentials, model IDs, app URL and billing table together. Cloud Run and CI are
already configured for the destination; local `.env` files are not their source.

To resume the cost-report schedule, first confirm that the target export contains
the previous Warsaw calendar day's costs, then resume `puls-gpw-cost-report-trigger`
in the target project's Scheduler console. Resume scraping and its monitoring
only after a successful read-only listing/article fetch from target Cloud Run.

Do not delete source resources, rotate primary source secrets, or write archives
or foundation documents. Keep source schedules and API intact until the final
freeze. Never have two active pipeline copies. No credential values, password
hashes, user records or unredacted exports in tracked files or tool output.
Keep source billing export as historical data and preserve account-wide cost
report semantics. The new Cloud owner is not a request to change SMTP sender.

## Rollback

Before target writes: source inventory records original scheduler states and API
ingress; restore those and keep destination schedulers inactive. Source image and
credentials remain available. After target writes: stop target writers, reconcile
target BQ/Firebase changes into source, verify, then restore source. Never blindly
resume stale source data.
