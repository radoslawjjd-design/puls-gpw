<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Auth Foundation (PUL-71) — Phase 2

- **Plan**: context/changes/pul-71-auth-foundation/plan.md
- **Scope**: Phase 2 of 6 (tabela BigQuery `users` + round-trip)
- **Date**: 2026-07-17
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 1 observation
- **Commit**: b53d6f2

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

Weryfikacja na żywo w sesji: drift MATCH ×5 (triplet users z REQUIRED w initial create, insert_user, upsert_user_login MERGE, startup hook, skrypt round-trip wzorem test_bq_user_portfolios.py). Jeden udokumentowany EXTRA: 2 mocki startup hooków w `tests/e2e/conftest.py` ściągnięte z fazy 6 — konieczne dla kryterium 2.2 (bez nich E2E uderza w realne BQ; lekcja conftest-bq-mocking); faza 6 doda resztę mocków (CRUD + Firebase). Safety: wszystkie wartości przez ScalarQueryParameter, nazwy kolumn wolne od reserved keywords, błędy w BigQueryError, cleanup w finally (DELETE zawężony do 2 testowych user_id). Kryteria: 86 passed (test_bigquery), 483 passed (pełna suita), round-trip na realnym BQ 2× zielony (w tym self-heal), ruff/mypy zero nowych błędów vs master.

## Findings

### F1 — MERGE przy loginie nie odświeża emaila

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: db/bigquery.py — upsert_user_login, gałąź WHEN MATCHED
- **Detail**: MATCHED aktualizuje tylko `last_login_at` — email w BQ zestarzałby się, gdyby kiedyś doszedł flow zmiany emaila w Firebase. Zgodne z kontraktem planu; zmiana emaila poza zakresem PUL-71 — notatka przyszłościowa.
- **Fix**: Nic teraz; przy ewentualnym tickecie na zmianę emaila dodać `email = S.email` do gałęzi MATCHED.
- **Decision**: ACCEPTED (poza zakresem PUL-71)

## Note

Status change.md pozostaje `implementing` — phase-scoped review w trakcie implementacji; `impl_reviewed` ustawi dopiero pełny przegląd po fazie 6.
