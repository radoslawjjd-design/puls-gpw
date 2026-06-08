# Infrastructure — puls-gpw

GCP project: `puls-gpw` | Region: `europe-central2` | Service account: `puls-gpw-runner@puls-gpw.iam.gserviceaccount.com`

---

## Cloud Run Jobs

| Job | Obraz | CMD | Opis |
|-----|-------|-----|------|
| `puls-gpw` | `puls-gpw:<sha>` | `uv run python main.py` | Scraper — pobiera ESPI/EBI z Bankier, parsuje, analizuje przez Gemini, zapisuje do BQ |
| `puls-gpw-post` | `puls-gpw:latest` | `uv run python post_main.py` | Post generator — pobiera top-N z BQ, generuje wątek X przez Gemini, waliduje supervisorem, wysyła email |

Oba joby używają tego samego obrazu Docker z Artifact Registry:
`europe-central2-docker.pkg.dev/puls-gpw/puls-gpw/puls-gpw`

CI/CD (`.github/workflows/deploy.yml`) aktualizuje oba joby przy każdym push na `master`.

### Konfiguracja wspólna

- CPU: 1 vCPU | RAM: 1 GiB | Timeout: 300 s
- Sekrety (Secret Manager): `gemini-api-key`, `smtp-host`, `smtp-port`, `smtp-user`, `smtp-password`, `owner-email`
- Env vars: `GOOGLE_CLOUD_PROJECT=puls-gpw`, `BIGQUERY_DATASET=espi_ebi`

### Argumenty post-joba per okno (Cloud Scheduler override)

Job `puls-gpw-post` akceptuje `--window {ranek,poludnie,wieczor}`. Bez flagi auto-wykrywa okno z aktualnego czasu warszawskiego.

---

## Cloud Scheduler

| Job | Cron (Warsaw) | Co odpala | Kiedy |
|-----|---------------|-----------|-------|
| `puls-gpw-trigger` | `*/15 * * * *` | `puls-gpw` (scraper) | Co 15 min, całą dobę |
| `puls-gpw-post-ranek` | `30 8 * * 1-5` | `puls-gpw-post` | Pon–Pt 08:30 |
| `puls-gpw-post-poludnie` | `0 13 * * 1-5` | `puls-gpw-post` | Pon–Pt 13:00 |
| `puls-gpw-post-wieczor` | `30 17 * * 1-5` | `puls-gpw-post` | Pon–Pt 17:30 |

Wszystkie schedulery używają OAuth z service account `puls-gpw-runner` do wywołania Cloud Run Jobs API.

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
