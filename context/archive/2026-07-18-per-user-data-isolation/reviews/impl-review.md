<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Per-User Data Isolation (PUL-74) — Full Plan

- **Plan**: context/changes/per-user-data-isolation/plan.md
- **Scope**: Full plan (5 phases, master...HEAD, 9 commits 8aea973..5502460)
- **Date**: 2026-07-19
- **Verdict**: APPROVED
- **Findings**: 0 critical, 2 warnings (both fixed in-session), 4 observations
- **Prior per-phase reviews**: impl-review-phase-1.md, impl-review-phase-3.md, impl-review-phase-4.md (fazy 2 i 5 zrecenzowane tutaj)

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS — fazy 2+5: 19/19 kontraktów MATCH, zero driftu; extras wyłącznie ochronne |
| Scope Discipline | PASS — 7/7 pozycji "What We're NOT Doing" respektowane w całym diffie |
| Safety & Quality | WARNING → fixed (F1, F2) |
| Architecture | PASS — failure-precedence 401 OK; brak endpointu z _get_user_id bez _get_role (sliding refresh zachowany); cache bez kolizji UID/UUID; lockstep tożsamości (F2 z p1) spełniony konstrukcyjnie |
| Pattern Consistency | PASS — lessons (reserved keywords / mocked-SQL round-trip / SPA races) czyste |
| Success Criteria | PASS — 593 passed 1 skipped; 12/12 tras per-user za _get_user_id; admin intact; runbook 5.3 wykonany na prodzie (5+2+18, potwierdzone przez ownera) |

## Findings

### F1 — Skrypt re-key: ślepa plama na nie-zbackfillowane wiersze (user_id IS NULL)

- **Severity**: ⚠️ WARNING · **Impact**: 🏃 LOW
- **Location**: scripts/migrate_owner_identity.py
- **Detail**: Predykat `WHERE user_id = @old_uuid` po cichu pomijał wiersze z `user_id IS NULL` (backfill nie zbiegł / padał non-fatalnie) — `matched: 0` bez sygnału.
- **Fix (applied)**: pre-check `COUNT(*) WHERE user_id IS NULL AND client_id = @old_uuid` → abort z komunikatem o niezbiegłym backfillu; + testy (6/6). Przy okazji F4 (COUNT bez zbędnego @new_uid) i F5 (czytelny komunikat zero-match na realnym runie).
- **Decision**: FIXED

### F2 — Migration Notes: zawyżona obietnica rollbacku po re-key

- **Severity**: ⚠️ WARNING · **Impact**: 🏃 LOW
- **Location**: plan.md (Migration Notes)
- **Detail**: "dual-write guarantees the previous revision still reads client_id correctly" — po re-key prawdziwe tylko dla ścieżki JWT; anonimowa ścieżka zwraca pustkę (client_id przepisany na UID, frontend kasuje przeglądarkowy UUID).
- **Fix (applied)**: dopisek o niuansie rollbacku, wpis o wykonanym runbooku (5+2+18 + TTL cache), rozszerzenie chore DROP-a o `static/index_old.html` i linię `removeItem`.
- **Decision**: FIXED

### F3 — Podwójny decode JWT per request (\_get_role + \_get_user_id)

- ℹ️ OBSERVATION — mikrosekundy przy HS256; memo w request.state możliwe kiedyś. **Decision**: SKIPPED (kosmetyka)

### F4 — COUNT wiązał nieużywany @new_uid · **Decision**: FIXED (razem z F1)

### F5 — Mylący komunikat zero-match na realnym runie · **Decision**: FIXED (razem z F1)

### F6 — static/index_old.html: ostatnia żywa kopia mechaniki X-Client-Id

- ℹ️ OBSERVATION — nieszkodliwa (backend zwraca 401), ale osiągalna przez static mount. **Decision**: ACKNOWLEDGED — usunięcie dopisane do chore DROP-a (F2 fix)

## Resolved flags

- **Progress 5.3 przed merge**: zasadne — kolumna `watchlist.user_id` + backfill trafiły na prod BQ podczas round-tripu Fazy 1.3 (`scripts/test_bq.py`), a re-key wykonano 2026-07-19 z potwierdzeniem ownera. Skrypt działa na BQ niezależnie od wdrożonego kodu.
- Konstrukcja testów izolacji (jedno cookie B + mocki zamiast dwóch cookies) — semantycznie równoważna kontraktowi planu; scenariusz literalny A/B istnieje na poziomie przeglądarki (e2e spot-check, p4).
