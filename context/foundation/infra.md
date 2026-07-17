# Infrastructure — puls-gpw

GCP project: `puls-gpw` | Region: `europe-central2` | Service account: `puls-gpw-runner@puls-gpw.iam.gserviceaccount.com`

---

## Cloud Run Jobs

| Job | Obraz | CMD | Opis |
|-----|-------|-----|------|
| `puls-gpw` | `puls-gpw:<sha>` | `uv run python main.py` | Scraper — pobiera ESPI/EBI z Bankier, parsuje, analizuje przez Gemini, zapisuje do BQ |
| `puls-gpw-post` | `puls-gpw:<sha>` | `uv run python post_main.py` | Post generator — pobiera top-N z BQ, generuje wątek X przez Gemini, waliduje supervisorem, wysyła email |
| `puls-gpw-company-stats` | `puls-gpw:<sha>` | `uv run python company_stats_main.py` | Daily stats snapshot — pobiera dane z Bankier listing pages (GPW + NewConnect), mapuje na companies, zapisuje do company_daily_stats |

Wszystkie trzy joby używają tego samego obrazu Docker z Artifact Registry:
`europe-central2-docker.pkg.dev/puls-gpw/puls-gpw/puls-gpw`

CI/CD (`.github/workflows/deploy.yml`) aktualizuje oba joby przy każdym push na `master`.

### Konfiguracja wspólna

- CPU: 1 vCPU | RAM: 1 GiB | Timeout: 300 s
- Sekrety (Secret Manager): `gemini-api-key`, `smtp-host`, `smtp-port`, `smtp-user`, `smtp-password`, `owner-email`
- Env vars: `GOOGLE_CLOUD_PROJECT=puls-gpw`, `BIGQUERY_DATASET=espi_ebi`

> **Komenda obu jobów jest ustawiana jawnie w `deploy.yml`** (`--command=uv --args=run,python,<entry>`),
> a nie polegamy już na konfiguracji utrwalonej przy `gcloud run jobs create`. Sekrety/env post-joba są
> dokładane **addytywnie** (`--update-secrets` / `--update-env-vars`), więc deploy nie kasuje istniejących
> sekretów SMTP/Gemini.

### Publikacja na X (`puls-gpw-post`)

Job `puls-gpw-post` może publikować zatwierdzony wątek bezpośrednio na X (Twitter), gdy włączony jest flag.

- **Sekrety (Secret Manager)** — OAuth 1.0a user-context, 4 osobne sekrety:
  `x-api-key`, `x-api-secret`, `x-access-token`, `x-access-secret`
  → wstrzykiwane jako env `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`.
- **Env var**: `X_AUTO_PUBLISH` — domyślnie `false` (deploy ustawia `false`). `true` = auto-publikacja.
- **Wartości sekretów ustawia człowiek** (CLAUDE.md: tworzenie/rotacja sekretów = human-only); runner SA
  `puls-gpw-runner@` musi mieć `secretmanager.secretAccessor`.

**Dwuwarstwowe bezpieczeństwo (kill-switch):**
1. `X_AUTO_PUBLISH=false` (domyślnie) → wątek tylko na maila, **nic nie idzie na X**. Przełączenie na
   `true` to świadoma decyzja człowieka.
2. Schedulery (`puls-gpw-post-*`) można **wstrzymać** (`gcloud scheduler jobs pause`) → job w ogóle się
   nie odpala.

### Argumenty post-joba per okno (Cloud Scheduler override)

Job `puls-gpw-post` akceptuje `--window {ranek,poludnie,wieczor}`. Bez flagi auto-wykrywa okno z aktualnego czasu warszawskiego.

---

## Cloud Run Service — `puls-gpw-api`

Serwis API (FastAPI, `api_main.py`), deployowany przez CI przy każdym push na `master`
(`deploy.yml`, krok "Deploy Cloud Run Service (api)"). `--set-secrets`/`--set-env-vars`
mają **replace semantics** — zawsze podawaj pełną listę.

### Sekrety (Secret Manager)

| Sekret | Env var | Opis |
|--------|---------|------|
| `admin-api-key` | `ADMIN_API_KEY` | Klucz API roli admin |
| `user-api-key` | `USER_API_KEY` | Klucz API roli user |
| `jwt-secret` | `JWT_SECRET` | Klucz HS256 do podpisywania JWT sesji (PUL-71); generowany np. `openssl rand -hex 32` |
| `firebase-service-account` | `FIREBASE_SERVICE_ACCOUNT_JSON` | Treść JSON klucza SA `firebase-adminsdk-fbsvc@` (nie ścieżka) — Firebase Admin SDK (PUL-71) |

**Wartości sekretów ustawia człowiek** (CLAUDE.md: tworzenie/rotacja sekretów = human-only);
runner SA `puls-gpw-runner@` musi mieć `secretmanager.secretAccessor`.

### Env vars

`GOOGLE_CLOUD_PROJECT=puls-gpw`, `BIGQUERY_DATASET=espi_ebi`,
`FIREBASE_WEB_API_KEY` (klucz **publiczny** Web API Firebase — identyfikuje projekt przy
Identity Toolkit REST, nie jest sekretem; wartość w `deploy.yml`).

---

## Cloud Scheduler

| Job | Cron (Warsaw) | Co odpala | Kiedy |
|-----|---------------|-----------|-------|
| `puls-gpw-trigger` | `*/15 * * * *` | `puls-gpw` (scraper) | Co 15 min, całą dobę |
| `puls-gpw-post-ranek` | `30 8 * * 1-5` | `puls-gpw-post` | Pon–Pt 08:30 |
| `puls-gpw-post-poludnie` | `0 13 * * 1-5` | `puls-gpw-post` | Pon–Pt 13:00 |
| `puls-gpw-post-wieczor` | `30 17 * * 1-5` | `puls-gpw-post` | Pon–Pt 17:30 |
| `puls-gpw-company-stats-trigger` | `1,31 9-17 * * 1-5` | `puls-gpw-company-stats` | Pon–Pt co 30 min 9:01–17:31 (18 razy/dzień) |

Wszystkie schedulery używają OAuth z service account `puls-gpw-runner` do wywołania Cloud Run Jobs API.

### One-time provisioning runbook — `puls-gpw-company-stats`

> **HUMAN-ONLY** (per CLAUDE.md). Wykonaj raz przed pierwszym pushiem do `master` z nowym krokiem w `deploy.yml`.
> CPU/RAM/sekrety/env vars identyczne z istniejącą wspólną konfiguracją — ten job nie wymaga nowych sekretów.
> Timeout: **300 s** (job działa ~6 s — 2 fetche listing pages + 1 BQ streaming insert; standard budżet w pełni wystarczy).

```bash
# 1. Utwórz job
gcloud run jobs create puls-gpw-company-stats \
  --image=europe-central2-docker.pkg.dev/puls-gpw/puls-gpw/puls-gpw:latest \
  --command=uv --args="run,--no-dev,python,company_stats_main.py" \
  --region=europe-central2 \
  --project=puls-gpw \
  --service-account=puls-gpw-runner@puls-gpw.iam.gserviceaccount.com \
  --set-secrets="GEMINI_API_KEY=gemini-api-key:latest,SMTP_HOST=smtp-host:latest,SMTP_PORT=smtp-port:latest,SMTP_USER=smtp-user:latest,SMTP_PASSWORD=smtp-password:latest,OWNER_EMAIL=owner-email:latest" \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=puls-gpw,BIGQUERY_DATASET=espi_ebi" \
  --cpu=1 --memory=1Gi \
  --task-timeout=300s

# 2. Utwórz trigger Cloud Scheduler (co godz. 9:01–17:01, Pon–Pt, czas warszawski)
gcloud scheduler jobs create http puls-gpw-company-stats-trigger \
  --schedule="1,31 9-17 * * 1-5" \
  --time-zone="Europe/Warsaw" \
  --uri="https://europe-central2-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/puls-gpw/jobs/puls-gpw-company-stats:run" \
  --http-method=POST \
  --oauth-service-account-email=puls-gpw-runner@puls-gpw.iam.gserviceaccount.com \
  --location=europe-central2 \
  --project=puls-gpw

# 3. Weryfikacja
gcloud run jobs list --region=europe-central2 --project=puls-gpw
gcloud scheduler jobs list --location=europe-central2 --project=puls-gpw
```

---

## Okna czasowe post-generatora

| Okno | Przedział (Warsaw) | Uwagi |
|------|-------------------|-------|
| `ranek` | wczoraj 17:31 → dziś 08:29 | Obejmuje noc + wczesny ranek; jeśli < 1 zatwierdzonej spółki → no-post email |
| `poludnie` | dziś 08:30 → 12:59 | Sesja otwarta; jeśli < 1 spółki → milczy (brak emaila) |
| `wieczor` | dziś 13:00 → 17:29 | Sesja trwa; jeśli < 1 spółki → no-post email |

---

## BigQuery

Dataset: `puls-gpw.espi_ebi` | Tabela: `announcements`

Kluczowe kolumny: `announcement_id`, `ticker`, `company`, `published_at`, `analysis_approved`, `analysis_score`, `post_text`, `supervisor_attempts`, `processed_at`.

---

## Przepływ danych

```
Bankier.pl
    │  co 15 min (scraper)
    ▼
Cloud Run: puls-gpw (main.py)
    │  scrape → parse → analyze (Gemini) → save
    ▼
BigQuery: announcements
    │  3x dziennie (08:30 / 13:00 / 17:30)
    ▼
Cloud Run: puls-gpw-post (post_main.py)
    │  fetch top-N → generate (Gemini) → validate (supervisor)
    ▼
Email → właściciel (gotowy wątek X do wklejenia)
```
