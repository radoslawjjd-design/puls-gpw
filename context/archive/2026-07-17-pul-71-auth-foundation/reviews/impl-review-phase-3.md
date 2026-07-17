<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Auth Foundation (PUL-71) — Phase 3

- **Plan**: context/changes/pul-71-auth-foundation/plan.md
- **Scope**: Phase 3 of 6 (rdzeń auth — walidacje, JWT, rate limiter; TDD)
- **Date**: 2026-07-17
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 2 observations (both FIXED in-session)
- **Commit**: d08a548 (faza) + follow-up commit z fixami F1/F2

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

Weryfikacja na żywo: drift MATCH ×3 (walidacje RegisterIn/LoginIn z strip→email_validator i regułą 8-128+litera+cyfra pinowaną na granicach; JWT HS256 z payloadem {user_id,email,auth_type,iat,exp}, cookie `session`, decode→None, JWT_SECRET call-time; rate limiter deque+lock, okno 60s, OSTATNI element XFF, wstrzykiwalny time_fn, test anty-spoofingowy). Zero EXTRA. TDD: 3 przebiegi RED→GREEN z RED z właściwego powodu; 1 poprawka kodu pod testem (Retry-After 61→ceil), 1 poprawka testu (asyncio.run→napęd korutyny — interferencja event loopa z wcześniejszych testów uvicorn/E2E w pełnej suicie). Kryteria: 26/26 testów auth, pełna suita 509 passed, ruff+mypy nowe pliki 100% czyste.

## Findings

### F1 — _jwt_secret() zwracał "" bez guardu

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/auth.py — create_session_token / decode_session_token
- **Detail**: Przy braku env vara funkcje JWT podpisywałyby/weryfikowały pustym stringiem. Prod chroniony guardem w api_main.py, ale proces uruchomiony inną ścieżką akceptowałby tokeny podpisane pustym sekretem.
- **Fix**: create_session_token → RuntimeError przy pustym sekrecie; decode_session_token → None przy pustym sekrecie. Oba pinowane testami (test_create_session_token_requires_secret, test_decode_returns_none_without_secret).
- **Decision**: FIXED (in-session, TDD)

### F2 — kubełki rate limitera nie były usuwane po opróżnieniu

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/auth.py — RateLimiter.check()
- **Detail**: Klucz IP zostawał w dict na zawsze — powolny wzrost pamięci przy wielu unikalnych IP (prune-on-access nie sprząta porzuconych kluczy).
- **Fix**: Globalny sweep przeterminowanych kubełków przy rozmiarze dict ≥ sweep_threshold (domyślnie 1000), wykonywany pod lockiem. Pinowany testem (test_rate_limiter_sweeps_stale_buckets).
- **Decision**: FIXED (in-session, TDD)

## Note

Status change.md pozostaje `implementing` — phase-scoped review; `impl_reviewed` ustawi pełny przegląd po fazie 6.
