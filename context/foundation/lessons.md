# Lessons Learned

> Append-only register of recurring rules and patterns. Re-read at start by /10x-frame, /10x-research, /10x-plan, /10x-plan-review, /10x-implement, /10x-impl-review.

## GCP client initialization — load_dotenv + ADC quota project

**Context**: db/bigquery.py, main.py — każdy moduł inicjalizujący klienta Google Cloud

**Problem**: Plan nie uwzględnił dwóch operacyjnych wymagań odkrytych przy F-02:
1. `load_dotenv()` musi być wywołane w entry point *przed* importami modułów GCP
   — `BIGQUERY_DATASET` i `GOOGLE_CLOUD_PROJECT` są czytane przy imporcie modułu
2. Lokalny ADC może mieć `quota_project_id` ustawiony na inny projekt niż
   `GOOGLE_CLOUD_PROJECT`, co powoduje 403 na każdym wywołaniu BQ API

**Rule**: Przy planowaniu każdego change'a który:
- Dodaje nowy klient GCP (BQ, Cloud Storage, Pub/Sub, itp.) — uwzględnij
  `with_quota_project` guard (z `hasattr`) w fazie inicjalizacji klienta
- Dodaje nowy entry point skrypt — uwzględnij `load_dotenv()` jako pierwszy
  import przed jakimkolwiek modułem czytającym env vars

**Applies to**: Każdy change z nowym klientem GCP lub nowym entry pointem pipeline'u
