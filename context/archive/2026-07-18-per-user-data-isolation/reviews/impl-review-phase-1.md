<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Per-User Data Isolation (PUL-74) — Phase 1

- **Plan**: context/changes/per-user-data-isolation/plan.md
- **Scope**: Phase 1 of 5 (commit 8aea973)
- **Date**: 2026-07-18
- **Verdict**: APPROVED
- **Findings**: 0 critical, 1 warning, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS (5/5 MATCH, zero drift/scope-creep) |
| Scope Discipline | PASS |
| Safety & Quality | WARNING (F1, fixed in-session) |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS (580 unit+e2e, 92 BQ po fixie, round-trip na realnym BQ) |

## Findings

### F1 — Backfill jako powierzchnia awarii startupu (NotFound + DML co cold start)

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — realny tradeoff; warto się zatrzymać
- **Dimension**: Safety & Quality (reliability)
- **Location**: db/bigquery.py:497-515 (+ startup hook src/api.py:282-293)
- **Detail**: `_backfill_watchlist_user_id` łamał graceful-NotFound kontrakt rodzeństwa `ensure_*` i odpalał mutujący DML na każdym cold starcie bez try/except w hooku startowym — transient błąd BQ (5xx, kolizja równoległych UPDATE'ów) = crash-loop instancji Cloud Run.
- **Fix A ⭐ (applied)**: NotFound → graceful return; inne błędy → `logger.warning` + kontynuacja startupu. Idempotencja gwarantuje konwergencję na kolejnym cold starcie. Nowy test: `test_watchlist_backfill_is_non_fatal_on_bq_error`.
- **Decision**: FIXED via Fix A

### F2 — Identity-lockstep przy MERGE (anonimowe wiersze portfela)

- **Severity**: ℹ️ OBSERVATION
- **Dimension**: Data safety
- **Location**: db/bigquery.py:572
- **Detail**: MERGE key z `user_id` oznacza, że jakiekolwiek ręczne przepisanie `user_id` w `user_portfolios` bez `user_portfolio_positions` zacząłby mnożyć duplikaty. Anonimowe wiersze portfela pozostają osierocone zgodnie z "What We're NOT Doing"; skrypt Fazy 5 przepisuje wszystkie trzy tabele razem.
- **Decision**: ACKNOWLEDGED — pilnować lockstepu w Fazie 5 (skrypt tak zaprojektowany)

### F3 — Okno rolling-deploy może stworzyć duplikat tickera

- **Severity**: ℹ️ OBSERVATION
- **Location**: db/bigquery.py (INSERT NOT EXISTS na user_id vs stare instancje piszące tylko client_id)
- **Detail**: W oknie deployu stara instancja może wstawić wiersz bez user_id; nowy INSERT go nie widzi → możliwy duplikat do następnego backfillu. Niski ruch; widoczność samoleczy się backfillem.
- **Decision**: ACCEPTED (ryzyko znikome, jednorazowe okno)

### F4 — scripts/test_bq.py: wartość stałej bez zmiany

- **Severity**: ℹ️ OBSERVATION
- **Location**: scripts/test_bq.py:42
- **Detail**: `TEST_WATCHLIST_USER_ID = "test-bq-integration-watchlist-client"` — nazwa nowa, wartość stara. Kosmetyka; kompatybilna z cleanupem starych przebiegów.
- **Decision**: ACCEPTED

## Note

`change.md.status` pozostaje `implementing` — to recenzja per-faza w trakcie planu 5-fazowego; status `impl_reviewed` zarezerwowany dla recenzji końcowej.
