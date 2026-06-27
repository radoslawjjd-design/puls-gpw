<!-- PLAN-REVIEW-REPORT -->
# Plan Review: company-stats-upsert

- **Plan**: `context/changes/company-stats-upsert/plan.md`
- **Mode**: Deep
- **Date**: 2026-06-27
- **Verdict**: SOUND (po poprawkach)
- **Findings**: 0 critical  2 warnings  0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | PASS |
| Plan Completeness | WARNING |

## Grounding

4/5 paths ✓ (scripts/test_bq_company_stats_merge.py — nowy plik, OK), 5/5 symbols ✓, brief↔plan ✓

## Findings

### F1 — create_disposition=CREATE_IF_NEEDED redundant w LoadJobConfig

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 1 — Changes Required §2, Contract
- **Detail**: Kontrakt mówił jednocześnie `create_table(exists_ok=True)` i `create_disposition=CREATE_IF_NEEDED` w LoadJobConfig. Po `create_table` tabela zawsze istnieje, CREATE_IF_NEEDED jest no-opem. Implementer mógł być zdezorientowany który mechanizm jest właściwy.
- **Fix**: Usunięto `create_disposition=CREATE_IF_NEEDED` z kontraktu LoadJobConfig. Zostaje: `schema=_COMPANY_DAILY_STATS_SCHEMA`, `write_disposition=WRITE_TRUNCATE`.
- **Decision**: FIXED

### F2 — Kontrakt testów pomijał setup mocka load_table_from_json

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 1 — Changes Required §3, Contract
- **Detail**: `_mock_bq_client()` konfiguruje tylko `client.query.return_value`. `client.load_table_from_json.return_value.errors` domyślnie zwraca truthy MagicMock → happy-path test fałszywie rzucałby BigQueryError.
- **Fix**: Dodano do kontraktu §3: wymóg ustawienia `client.load_table_from_json.return_value.errors = None` i `.result.return_value = None` w happy-path testach.
- **Decision**: FIXED
