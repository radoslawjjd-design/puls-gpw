---
deployed_at: 2026-05-18
project_id: puls-gpw
region: europe-central2
image: europe-central2-docker.pkg.dev/puls-gpw/puls-gpw/puls-gpw:initial
image_digest: sha256:5ee04a3386ca6c667e3fe13b2ee6dfde2074c36a7ff4dda6024756b77826b797
cloud_run_job: puls-gpw
scheduler_job: puls-gpw-trigger
schedule: "*/15 * * * *"
timezone: Europe/Warsaw
---

## What was deployed

First deployment of puls-gpw infrastructure to GCP. Scope: container image + Cloud Run Job + Cloud Scheduler + IAM + Secret Manager placeholders + monitoring. Pipeline code (scraper, parser, Gemini agent, supervisor, notifier) is NOT implemented — main.py is still a stub that prints "Hello from test-projekt!" and exits 0. Verification: job executed manually and completed successfully (exit 0, log confirmed).

## Resources created

| Resource | Name / ID |
|---|---|
| Artifact Registry repo | `europe-central2-docker.pkg.dev/puls-gpw/puls-gpw` |
| Artifact Registry lifecycle policy | keep-last-10 (images) |
| Container image | `puls-gpw:initial` — digest `sha256:5ee04a3386ca6c667e3fe13b2ee6dfde2074c36a7ff4dda6024756b77826b797` |
| Cloud Run Job | `puls-gpw` (region: `europe-central2`) |
| Cloud Scheduler job | `puls-gpw-trigger` (region: `europe-central2`) |
| Service account — runner | `puls-gpw-runner@puls-gpw.iam.gserviceaccount.com` |
| Service account — scheduler | `puls-gpw-scheduler@puls-gpw.iam.gserviceaccount.com` |
| Cloud Monitoring alert | `projects/puls-gpw/alertPolicies/5888120520158610756` |

## Cloud Run Job configuration

- `--max-retries=0` — supervisor gate owns all retry logic (prevents dual-retry collision)
- `--task-timeout=300s` — 5-min ceiling prevents runaway free-tier consumption
- `--memory=512Mi --cpu=1` — baseline for PDF parsing + Gemini responses
- `--service-account=puls-gpw-runner@...` — minimal-privilege runner SA

## Secret Manager secrets (placeholders — fill before first real run)

All secrets created in `europe-central2` with placeholder value `"placeholder"`. Replace via:
```
printf "ACTUAL_VALUE" | gcloud secrets versions add SECRET_NAME --data-file=-
```

| Secret name | Env var | Status |
|---|---|---|
| `gemini-api-key` | `GEMINI_API_KEY` | **NEEDS REAL VALUE** |
| `smtp-host` | `SMTP_HOST` | **NEEDS REAL VALUE** |
| `smtp-port` | `SMTP_PORT` | **NEEDS REAL VALUE** |
| `smtp-user` | `SMTP_USER` | **NEEDS REAL VALUE** |
| `smtp-password` | `SMTP_PASSWORD` | **NEEDS REAL VALUE** |
| `owner-email` | `OWNER_EMAIL` | **NEEDS REAL VALUE** |

## IAM granted

| Principal | Role |
|---|---|
| `puls-gpw-runner` SA | `roles/secretmanager.secretAccessor` |
| `puls-gpw-runner` SA | `roles/bigquery.dataEditor` |
| `puls-gpw-runner` SA | `roles/bigquery.jobUser` |
| `puls-gpw-scheduler` SA | `roles/run.invoker` |

## Human-gated items (not done by agent)

1. **Fill secret values** — replace all 6 placeholder secrets with real values before pipeline code is implemented.
2. **Set billing alert** — GCP Console → Billing → Budgets & alerts → Create budget at $5/month threshold.
3. **Add notification channel to monitoring alert** — GCP Console → Monitoring → Alerting → Edit policy `5888120520158610756` → add email notification channel.
4. **Verify BigQuery dataset region** — confirm `espi_ebi` dataset is in `europe-central2` to avoid cross-region egress charges.

## Verification result

```
Execution [puls-gpw-rllv5] has successfully completed.
Logs:
  2026-05-18T21:48:49.943167Z  Hello from test-projekt!
  2026-05-18T21:48:50.003914Z  Container called exit(0).
```

## Next steps

1. Implement pipeline code: `src/scraper.py`, `src/parser.py`, `src/agent.py`, `src/supervisor.py`, `src/notifier.py`
2. Update `main.py` to orchestrate the pipeline stages
3. Fill in Secret Manager secrets with real values
4. Wire GitHub Actions CI/CD (`auto-deploy-on-merge` → `gcloud builds submit` + `gcloud run jobs update`)
5. Rebuild and redeploy image after pipeline implementation: `gcloud builds submit --tag=...:$(git rev-parse --short HEAD)`
