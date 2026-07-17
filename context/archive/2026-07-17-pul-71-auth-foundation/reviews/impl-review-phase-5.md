<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Auth Foundation (PUL-71) — Phase 5

- **Plan**: context/changes/pul-71-auth-foundation/plan.md
- **Scope**: Phase 5 of 6 (rozszerzenie auth seam; TDD)
- **Date**: 2026-07-17
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 1 observation (design note → PUL-72)
- **Commit**: 300ae91

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

Weryfikacja na żywo: drift MATCH — kolejność z ticketa (JWT cookie → user; API-key bez zmian; nic → 401), nieważny/wygasły cookie = fallthrough na nagłówki (pinowane testem), sliding refresh przez `response: Response` w dependency (próg 24h; świeży token bez re-emisji — oba pinowane), `_get_client_id` bierze user_id z JWT (grunt pod PUL-74). Sygnatury dla ~30 call site'ów nietknięte. Drobiazg strukturalny: helpery sesji (`session_payload_from_request`, `refresh_session_if_stale`) w src/auth.py zamiast inline w src/api.py — lepsza kohezja, bez wpływu na kontrakt. TDD: 1 przebieg RED→GREEN, 7 nowych testów; istniejące 98 testów test_api.py bez modyfikacji. Kryteria: pełna suita 537 passed, ruff czysty, mypy zero nowych błędów (2 zastane w portfolio-kodzie, linie przesunięte). Manual 5.3 wykonany 2× na realnym Firebase+BQ: /announcements z samym cookie 200 (realne dane widoku user), z samym API-key 200, bez credentials 401, /admin/* z cookie 403; smoke userzy posprzątani (Firebase + BQ).

## Findings

### F1 — cookie przesłania admin API-key gdy oba obecne

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/api.py — _get_role (kolejność sprawdzeń)
- **Detail**: Admin z kluczem w localStorage, który zaloguje się też przez Firebase w tej samej przeglądarce, spada do roli user (cookie wygrywa; /admin/* → 403 do czasu logout). Wprost kolejność z ticketa — nie bug; istotne dla projektu UI logowania w PUL-72.
- **Fix**: Design note dopisana do change.md (sekcja Notes) — do uwzględnienia przy planowaniu PUL-72.
- **Decision**: FIXED (design note zapisana)

## Note

Status change.md pozostaje `implementing` — phase-scoped review; `impl_reviewed` ustawi pełny przegląd po fazie 6.
