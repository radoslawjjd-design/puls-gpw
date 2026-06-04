# F-02: BigQuery Schema `announcements` — Plan Brief

> Full plan: `context/changes/bigquery-schema/plan.md`

## What & Why

Tworzymy tabelę `announcements` w BigQuery i wrapper Python do jej obsługi. Bez tej foundations S-01 (scraper) nie ma gdzie sprawdzać duplikatów, a S-03 (analiza AI) nie ma gdzie zapisywać wyników.

## Starting Point

Dataset `espi_ebi` w `europe-central2` już istnieje. Projekt nie ma `google-cloud-bigquery` w zależnościach ani żadnego kodu bazodanowego — `main.py` to stub.

## Desired End State

Tabela `espi_ebi.announcements` z 10 polami. `db/bigquery.py` eksportuje `is_processed()`, `insert_announcement()`, `save_analysis()`. `main.py` tworzy tabelę przy starcie. Skrypt `scripts/test_bq.py` potwierdza end-to-end round-trip.

## Key Decisions Made

| Decyzja | Wybór | Dlaczego |
|---|---|---|
| Klucz dedup (`announcement_id`) | SHA-256 hex z `bankier_url` | Stabilny, deterministyczny, nie wymaga zewnętrznego ID |
| Schema | 10 pól: roadmap default + `analysis_type` | `analysis_type` potrzebny w S-03 — dodanie teraz = zero migracji |
| Auth | ADC lokalnie / IAM w Cloud Run | Zero credentials w kodzie; ten sam mechanizm oba środowiska |
| Tworzenie tabeli | Code-driven `create_if_not_exists` | Idempotentny, działa w Cloud Run bez ręcznych kroków |
| Wrapper shape | Moduł funkcyjny `db/bigquery.py` | Minimal, łatwy do mockowania w Module 3 |
| Testowanie | Skrypt ręczny `scripts/test_bq.py` | Weryfikuje prawdziwe BQ; spójny ze stylem F-01 |

## Scope

**In scope:** `google-cloud-bigquery` dep, `db/bigquery.py` z 4 funkcjami, integracja z `main.py`, skrypt testowy

**Out of scope:** Migracje schematu, testy jednostkowe z mockiem, partycjonowanie, inne tabele

## Architecture / Approach

```
main.py
  └── db/bigquery.py
        ├── _client()          ← ADC / IAM, lazy init
        ├── create_table_if_not_exists()
        ├── is_processed(url)  ← używane przez S-01
        ├── insert_announcement(...)  ← używane przez S-01
        └── save_analysis(...)  ← używane przez S-03
```

Auth: `google.cloud.bigquery.Client()` automatycznie używa ADC lokalnie i service account IAM w Cloud Run. Zero konfiguracji w kodzie.

## Phases at a Glance

| Phase | Co dostarcza | Kluczowe ryzyko |
|---|---|---|
| 1. Dependency + Schema | `google-cloud-bigquery` w deps, schemat zdefiniowany, tabela tworzona przy starcie | `uv.lock` musi być zaktualizowany przed buildem Docker |
| 2. Wrapper Functions | `is_processed`, `insert_announcement`, `save_analysis` gotowe | DML UPDATE w BQ wymaga `WRITE_APPEND` + odpowiednich uprawnień IAM |
| 3. Integration + Test | `main.py` wired, round-trip potwierdzony skryptem | Credentials lokalnie (ADC musi być skonfigurowane) |

**Prerequisites:** Dataset `espi_ebi` istnieje w `europe-central2` ✓; `gcloud auth application-default login` wykonane lokalnie
**Estimated effort:** ~1 sesja, 3 fazy

## Open Risks & Assumptions

- Service account Cloud Run musi mieć role `BigQuery Data Editor` + `BigQuery Job User` — zakładamy że jest skonfigurowany (infrastruktura istniejąca)
- `google-cloud-bigquery` ciągnie za sobą duże transitive deps (`google-auth`, `googleapis-common-protos` itp.) — rozmiar obrazu Docker wzrośnie

## Success Criteria (Summary)

- `uv run scripts/test_bq.py` przechodzi end-to-end bez błędów
- Tabela `espi_ebi.announcements` widoczna w BigQuery Console z 10 polami
- `uv run python main.py` kończy się kodem 0 w Cloud Run
