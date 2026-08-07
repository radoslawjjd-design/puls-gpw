# Infrastructure — puls-gpw

GCP project: `puls-gpw` | Region: `europe-central2` | Service account: `puls-gpw-runner@puls-gpw.iam.gserviceaccount.com`

---

## Cloud Run Jobs

| Job | Obraz | CMD | Opis |
|-----|-------|-----|------|
| `puls-gpw` | `puls-gpw:<sha>` | `uv run python main.py` | Scraper — pobiera ESPI/EBI z Bankier, parsuje, analizuje przez Gemini, zapisuje do BQ |
| `puls-gpw-post` | `puls-gpw:<sha>` | `uv run python post_main.py` | Post generator — pobiera top-N z BQ, generuje wątek X przez Gemini, waliduje supervisorem, wysyła email |
| `puls-gpw-company-stats` | `puls-gpw:<sha>` | `uv run python company_stats_main.py` | Daily stats snapshot — pobiera dane z Bankier listing pages (GPW + NewConnect), mapuje na companies, zapisuje do company_daily_stats |
| `puls-gpw-etf-quotes` | `puls-gpw:<sha>` | `uv run python etf_quotes_main.py` | ETF/ETC/ETN — pobiera stronę notowań GPW, zapisuje instrumenty i kursy (PUL-67) |
| `puls-gpw-cost-report` | `puls-gpw:<sha>` | `uv run python cost_report_main.py` | Dzienny raport kosztów GCP — czyta billing export z BQ, wysyła maila z rozbiciem per usługa i per model Vertex, flaguje anomalię w temacie (PUL-125) |

Wszystkie pięć jobów używa tego samego obrazu Docker z Artifact Registry:
`europe-central2-docker.pkg.dev/puls-gpw/puls-gpw/puls-gpw`

CI/CD (`.github/workflows/deploy.yml`) aktualizuje wszystkie joby przy każdym push na `master`.

### Konfiguracja wspólna

- CPU: 1 vCPU | RAM: 1 GiB | Timeout: 300 s
- Sekrety (Secret Manager): `gemini-api-key`, `smtp-host`, `smtp-port`, `smtp-user`, `smtp-password`, `owner-email`
- Env vars: `GOOGLE_CLOUD_PROJECT=puls-gpw`, `BIGQUERY_DATASET=espi_ebi`

> **Powiadomienia event-driven (PUL-81 v2).** Scraper `puls-gpw` (`main.py`) po
> zapisaniu + analizie każdego komunikatu obserwowanej spółki wysyła inline maila
> do zapisanych watcherów (zastąpiło crona `puls-gpw-notifications`, wycofanego).
> Job niesie env `APP_BASE_URL=https://puls-gpw-api-5zlombicra-lm.a.run.app`
> (publiczny URL appki web, NIE domena nadawcy `gpw.okiem.ai`) do linku/logo w mailu;
> link w mailu: `{APP_BASE_URL}/?view=my-wallet`. Kod defaultuje do tego samego URL,
> więc brak env = ten sam link. Ustawiane przez człowieka:
> `gcloud run jobs update puls-gpw --update-env-vars="APP_BASE_URL=…"`.

> **Raport kosztów (`puls-gpw-cost-report`, PUL-125).** Job niesie dwa env-y, których
> pozostałe joby nie mają: `COST_ANOMALY_FACTOR` (mnożnik mediany z 7 dni; deploy
> ustawia `2.0`, więc zmiana progu to edycja `deploy.yml`, nie ręczna komenda) oraz
> `APP_BASE_URL` — ten sam co scraper, do logo w mailu. `APP_BASE_URL` **nie jest**
> ustawiany przez `deploy.yml` dla żadnego joba; siedzi tylko w konfiguracji utrwalonej
> przy `create`, dlatego runbook niżej podaje go jawnie. Kod defaultuje do tego samego
> URL, więc brak env = ten sam link.
>
> Job **nie potrzebuje nowych uprawnień** — `puls-gpw-runner@` ma już `bigquery.dataEditor`
> i `bigquery.jobUser`, a billing export leży w tym samym datasecie `espi_ebi`. Naszego
> DDL-a nigdy nie wolno na tę tabelę puścić: pisze ją Google.

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
| `firebase-web-api-key` | `FIREBASE_WEB_API_KEY` | Dedykowany klucz API `puls-gpw-backend-identitytoolkit` (restrykcja: TYLKO `identitytoolkit.googleapis.com`) — backend REST signInWithPassword. Używany wyłącznie server-side, więc trzymany jako sekret mimo że klucze web Firebase bywają publiczne; poprzednik (auto-created Browser key) wyciekł do publicznej historii gita i został zrotowany (2026-07-17) |

**Wartości sekretów ustawia człowiek** (CLAUDE.md: tworzenie/rotacja sekretów = human-only);
runner SA `puls-gpw-runner@` musi mieć `secretmanager.secretAccessor`.

### Env vars

`GOOGLE_CLOUD_PROJECT=puls-gpw`, `BIGQUERY_DATASET=espi_ebi`.

---

## Cloud Scheduler

| Job | Cron (Warsaw) | Co odpala | Kiedy |
|-----|---------------|-----------|-------|
| `puls-gpw-trigger` | `*/15 * * * *` | `puls-gpw` (scraper) | Co 15 min, całą dobę |
| `puls-gpw-post-ranek` | `25 8 * * 1-5` | `puls-gpw-post` | Pon–Pt 08:25 |
| `puls-gpw-post-poludnie` | `55 12 * * 1-5` | `puls-gpw-post` | Pon–Pt 12:55 |
| `puls-gpw-post-wieczor` | `25 17 * * 1-5` | `puls-gpw-post` | Pon–Pt 17:25 |
| `puls-gpw-company-stats-trigger` | `1,31 9-17 * * 1-5` | `puls-gpw-company-stats` | Pon–Pt co 30 min 9:01–17:31 (18 razy/dzień) |
| `puls-gpw-etf-quotes-trigger` | `1,31 9-17 * * 1-5` | `puls-gpw-etf-quotes` | Pon–Pt co 30 min 9:01–17:31, jak company-stats (PUL-67) |
| `puls-gpw-cost-report-trigger` | `0 9 * * *` | `puls-gpw-cost-report` | Codziennie 09:00, także w weekendy — koszty naliczają się co dzień (PUL-125) |

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

### One-time provisioning runbook — `puls-gpw-cost-report`

> **HUMAN-ONLY** (per CLAUDE.md). **Wykonaj ZANIM PR z PUL-125 wejdzie do `master`.**
> To nie jest kosmetyczna kolejność: `deploy.yml` robi wyłącznie `gcloud run jobs update`,
> nigdy `create`. Merge przed wykonaniem tego runbooka zapala krok deployu na czerwono
> i blokuje pipeline **wszystkim** zmianom, nie tylko tej.
>
> Żadnych nowych sekretów ani uprawnień — `puls-gpw-runner@` ma już dostęp do BQ, a billing
> export leży w tym samym datasecie. Job czyta ~1000 wierszy i kończy w kilka sekund;
> timeout 300 s to zapas, nie potrzeba.
>
> **Tag `:latest` NIE ISTNIEJE w tym rejestrze** — `deploy.yml` taguje wyłącznie
> `:<github.sha>`. Runbook company-stats wyżej mówi `:latest` i jest w tym punkcie
> błędny; `create` kończy się `Image not found`, ale **i tak zostawia zasób joba**, więc
> druga próba wita cię `Job already exists` i trzeba dokończyć przez `jobs update`.
> Podstaw najnowszy tag:
> `gcloud artifacts docker images list <repo> --include-tags --sort-by=~UPDATE_TIME --limit=1`
>
> Obraz przy `create` to tylko ziarno — pierwszy merge do `master` i tak podmieni go na
> świeży. Dlatego **`gcloud run jobs execute` ma sens dopiero PO mergu**: obraz sprzed
> tej gałęzi nie zawiera `cost_report_main.py`.

```bash
# 0. Najnowszy dostępny tag (NIE ma tagu `latest`)
gcloud artifacts docker images list \
  europe-central2-docker.pkg.dev/puls-gpw/puls-gpw/puls-gpw \
  --project=puls-gpw --include-tags --sort-by=~UPDATE_TIME --limit=1 \
  --format="value(tags)"

# 1. Utwórz job (podstaw tag z kroku 0)
gcloud run jobs create puls-gpw-cost-report \
  --image=europe-central2-docker.pkg.dev/puls-gpw/puls-gpw/puls-gpw:<TAG> \
  --command=uv --args="run,--no-dev,python,cost_report_main.py" \
  --region=europe-central2 \
  --project=puls-gpw \
  --service-account=puls-gpw-runner@puls-gpw.iam.gserviceaccount.com \
  --set-secrets="SMTP_HOST=smtp-host:latest,SMTP_PORT=smtp-port:latest,SMTP_USER=smtp-user:latest,SMTP_PASSWORD=smtp-password:latest,OWNER_EMAIL=owner-email:latest" \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=puls-gpw,BIGQUERY_DATASET=espi_ebi,COST_ANOMALY_FACTOR=2.0,APP_BASE_URL=https://puls-gpw-api-5zlombicra-lm.a.run.app" \
  --cpu=1 --memory=1Gi \
  --task-timeout=300s

# 2. Utwórz trigger Cloud Scheduler (codziennie 09:00, także weekendy)
gcloud scheduler jobs create http puls-gpw-cost-report-trigger \
  --schedule="0 9 * * *" \
  --time-zone="Europe/Warsaw" \
  --uri="https://europe-central2-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/puls-gpw/jobs/puls-gpw-cost-report:run" \
  --http-method=POST \
  --oauth-service-account-email=puls-gpw-runner@puls-gpw.iam.gserviceaccount.com \
  --location=europe-central2 \
  --project=puls-gpw

# 3. Weryfikacja — obie nazwy muszą się pojawić na listach
gcloud run jobs list --region=europe-central2 --project=puls-gpw
gcloud scheduler jobs list --location=europe-central2 --project=puls-gpw

# 4. Próbny przebieg — DOPIERO PO MERGU do master, nie teraz.
#    Obraz podstawiony w kroku 1 pochodzi sprzed tej gałęzi i nie zawiera
#    cost_report_main.py; deploy po mergu podmienia go na właściwy.
gcloud run jobs execute puls-gpw-cost-report --region=europe-central2 --project=puls-gpw --wait
```

> **Wykonane 2026-08-07** (PUL-125). Job i trigger stoją, oba `ENABLED`, pierwszy zaplanowany
> odpal 2026-08-08 09:00 Warsaw. Job niesie tymczasowo obraz `938db09` — pierwszy merge
> podmieni go na bieżący.

> **Rollback**: `gcloud scheduler jobs pause puls-gpw-cost-report-trigger`. Job przestaje się
> odpalać, nic innego od niego nie zależy — nie ma tabeli, którą pisze, ani endpointu, który
> ktoś woła. Sam job można zostawić.

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
