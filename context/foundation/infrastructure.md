---
project: puls-gpw
researched_at: 2026-05-18
recommended_platform: GCP Cloud Run Jobs + Cloud Scheduler
runner_up: Fly.io
context_type: mvp
tech_stack:
  language: python
  framework: fastapi
  runtime: cpython-3.13
---

## Recommendation

**Deploy on GCP Cloud Run Jobs, triggered by Cloud Scheduler.**

puls-gpw is a stateless scheduled pipeline — no persistent connections, single region, BigQuery and Gemini already on the same GCP project. Cloud Run Jobs + Cloud Scheduler are the only candidate in this research that offer a fully managed scheduled-batch pair with zero cross-service authentication friction: one GCP project, one IAM surface, one billing line, entirely within the free tier at 96 runs/day × 2 min. User familiarity with Cloud Run and BigQuery seals the tie-break.

## Platform Comparison

| Platform | CLI-first | Managed/Serverless | Agent-readable docs | Stable deploy API | MCP / Integration | Score |
|---|---|---|---|---|---|---|
| GCP Cloud Run Jobs | Pass | Pass | Partial | Pass | Partial | 4.5 |
| Fly.io | Pass | Partial | Pass | Pass | Partial | 4.0 |
| Railway | Pass | Pass | Partial | Partial | Partial | 3.5 |
| Render | Fail | Pass | Partial | Partial | Fail | 2.0 |

### Shortlisted Platforms

#### 1. GCP Cloud Run Jobs (Recommended)

Cloud Scheduler (managed cron) triggers a Cloud Run Job via the Cloud Run Admin API — both are GA, both are fully managed GCP primitives. The job runs a container in `europe-central2`, executes the pipeline, and exits. No always-on server. Free tier: 180k vCPU-seconds/month for Jobs; 96 runs/day × 2 min = ~23k vCPU-seconds — 13% of the free tier. `gcloud` CLI covers every operation: job creation, secret binding, log tailing, manual execution. BigQuery and Gemini API calls stay within the same GCP project with no cross-service auth friction. Agent-readable docs: Partial — GCP docs are HTML-rendered; GitHub samples exist but no published `llms.txt` for Cloud Run.

#### 2. Fly.io

Strongest combination of agent-readable docs (markdown on GitHub) and CLI quality (`flyctl`). Python via Dockerfile. Compute cost is negligible (~$0.01/month). The gap vs GCP: Fly.io has no managed cron equivalent to Cloud Scheduler — scheduled jobs require self-hosting Cron Manager (a separate Fly app) or external trigger. For a pipeline-first project this is meaningful operational overhead. No MCP server.

#### 3. Railway

Best developer experience for Python setup: auto-detects language, builds without a Dockerfile. CLI (`railway up`) is solid. $5/month Hobby base fee covers all compute at this scale. Gap: Railway has no built-in cron — external scheduling required (GitHub Actions or persistent sleeping process). Docs are web-rendered (less agent-readable). No MCP. Dropped from recommendation because the cron gap is load-bearing for this pipeline.

## Anti-Bias Cross-Check: GCP Cloud Run Jobs

### Devil's Advocate — Weaknesses

1. **Dockerfile required; uv layer caching is non-trivial.** `COPY pyproject.toml` must precede `COPY src/` for uv to cache dependency installations. A wrong order means full reinstall on every build (~3 min vs ~15 sec).
2. **Dual retry collision.** Cloud Run Jobs `--max-retries` stacks on top of the pipeline supervisor's 3-retry gate. Both set to 3 = 9 Gemini API calls per broken announcement. Cloud Run `--max-retries` should be set to 0 for this pipeline.
3. **Cloud Scheduler → Cloud Run IAM permission is silent on failure.** Cloud Scheduler needs `roles/run.invoker` on the job. A missing or revoked IAM binding causes Cloud Scheduler to return HTTP 403 — but the error appears only in Cloud Scheduler logs, not in Cloud Run logs. The job simply never runs, with no alert email because the failure is pre-pipeline.
4. **Execution history is bounded (~50 runs).** At 96 runs/day, 50 executions = less than 12 hours of visible history in the Cloud Run UI. Cloud Logging is mandatory from day one for operational visibility.
5. **Artifact Registry storage compounds over time.** Each code push creates a new container image (~400-600MB). The free tier for Artifact Registry is 0.5GB — exceeded on the second unique image. Lifecycle policies deleting images older than 10 versions are required.

### Pre-Mortem — How This Could Fail

Six weeks after launch, a GCP project IAM audit tightens service account permissions. The Cloud Scheduler service account loses `roles/run.invoker`. The scheduler continues logging "triggered" (HTTP 200 from the Admin API acceptance layer), but Cloud Run never starts the execution. Forty-eight hours of GPW announcements are missed before the owner notices the silence — no alert email fires because `PipelineStageError` never runs. Separately, a developer updates the Dockerfile Python version but forgets to push a new image. Because the job is configured with a mutable `latest` tag and `gcloud run jobs update` without `--image` doesn't force a re-pull, stale code runs for 11 days. A month later, a parsing bug causes jobs to run 45 minutes instead of 2. Without a billing alert configured, 180k free vCPU-seconds are consumed in 4 days; a $12 charge appears on the card.

### Unknown Unknowns

- **Cloud Scheduler fires HTTP to the Cloud Run Admin API, not gcloud.** Disabled Cloud Run API or project quota exhaustion causes silent scheduler failure visible only in Cloud Scheduler logs.
- **Cloud Run Jobs and Cloud Run Services use separate free tier quotas.** Both are 180k vCPU-seconds/month but tracked as distinct billing line items — not a shared pool.
- **`uv` in Docker requires `ENV UV_LINK_MODE=copy`.** Without it, uv attempts hard-links across filesystem boundaries and emits confusing warnings; installs may be non-reproducible.
- **BigQuery dataset region must match Cloud Run region.** Default BigQuery dataset creation targets `US`; cross-region egress from `europe-central2` to `US` is charged at $0.01/GB. Use `europe-central2` for the dataset.
- **`gcloud run jobs execute` is async by default.** Returns immediately after submission; use `--wait` to block until completion and capture exit code in scripts.

## Operational Story

- **Preview deploys**: Cloud Run Jobs has no preview URL concept (it's a batch runner, not an HTTP server). Test locally with `uv run python main.py`; staging is a second Cloud Run Job in the same project with a separate Cloud Scheduler schedule. Agent creates the staging job using the same `gcloud run jobs create` command with a `--job-name puls-gpw-staging` flag.
- **Secrets**: All secrets (GEMINI_API_KEY, SMTP credentials, BigQuery service account) live in GCP Secret Manager. Bound to the job via `gcloud run jobs update JOB --set-secrets=GEMINI_API_KEY=gemini-api-key:latest`. Agent may update secret versions; rotating the primary key (deleting old versions) is human-only.
- **Rollback**: `gcloud run jobs update JOB --image=IMAGE_DIGEST` to pin to a previous container image from Artifact Registry. Time-to-rollback: ~30 seconds. Database (BigQuery) schema changes do not roll back automatically — migration reversals are a separate human-owned step.
- **Approval gates**: Agent may create/update Cloud Run Jobs, bind secrets, update Cloud Scheduler schedules, and tail logs. Human-only: drop a BigQuery table, delete a Cloud Run Job, rotate a primary GCP secret, modify IAM roles.
- **Logs**: `gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=puls-gpw" --limit=50 --format=json` streams the last 50 log entries as JSON. For execution history: `gcloud run jobs executions list --job=puls-gpw --region=europe-central2`.

## Risk Register

| Risk | Source | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| Cloud Scheduler IAM binding silently dropped | Devil's advocate | Medium | High | Add a Cloud Monitoring alerting policy on `cloud_run_job/completed_execution_count` — alert if count drops to 0 for >30 min |
| Dual retry collision (9 Gemini calls per broken announcement) | Devil's advocate | High | Medium | Set `--max-retries=0` on the Cloud Run Job; let the pipeline supervisor own all retry logic |
| Stale container image from mutable `latest` tag | Pre-mortem | Medium | Medium | Use immutable image tags (git SHA); configure Cloud Build to tag with `$COMMIT_SHA` and update job image on each push |
| Free tier exhausted by runaway job (parsing bug → long execution) | Pre-mortem | Low | Medium | Set Cloud Run Job `--task-timeout=300s` (5 min ceiling); configure billing alert at $5/month |
| Artifact Registry storage overrun | Devil's advocate | High | Low | Add Artifact Registry lifecycle policy: keep last 10 images, delete older; enforce via `gcloud artifacts repositories set-cleanup-policies` |
| BigQuery cross-region egress charges | Unknown unknowns | Medium | Low | Create BigQuery dataset in `europe-central2`; validate with `bq show --format=json PROJECT:DATASET \| jq .location` |
| uv Docker layer cache miss on every build | Devil's advocate | High | Low | Correct Dockerfile layer order: `COPY pyproject.toml uv.lock ./` → `RUN uv sync --frozen` → `COPY src/ ./src/` |
| `gcloud run jobs execute` async — scripts miss failures | Unknown unknowns | Medium | Medium | Always use `gcloud run jobs execute JOB --wait --region=REGION` in CI scripts; check exit code |

## Getting Started

1. **Enable APIs**: `gcloud services enable run.googleapis.com cloudscheduler.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com`
2. **Create Artifact Registry repository**: `gcloud artifacts repositories create puls-gpw --repository-format=docker --location=europe-central2`
3. **Build and push image** (from repo root with Dockerfile): `gcloud builds submit --tag europe-central2-docker.pkg.dev/PROJECT_ID/puls-gpw/puls-gpw:$(git rev-parse --short HEAD)`
4. **Create Cloud Run Job**: `gcloud run jobs create puls-gpw --image=europe-central2-docker.pkg.dev/PROJECT_ID/puls-gpw/puls-gpw:SHA --region=europe-central2 --max-retries=0 --task-timeout=300s --set-secrets=GEMINI_API_KEY=gemini-api-key:latest`
5. **Create Cloud Scheduler trigger**: `gcloud scheduler jobs create http puls-gpw-trigger --schedule="*/15 * * * *" --uri="https://europe-central2-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/PROJECT_ID/jobs/puls-gpw:run" --message-body="{}" --oauth-service-account-email=SCHEDULER_SA@PROJECT_ID.iam.gserviceaccount.com --location=europe-central2`

## Out of Scope

The following were not evaluated in this research:
- Docker image configuration and Dockerfile authorship
- CI/CD pipeline setup (Cloud Build trigger wiring)
- Production-scale architecture (multi-region, HA, DR)
