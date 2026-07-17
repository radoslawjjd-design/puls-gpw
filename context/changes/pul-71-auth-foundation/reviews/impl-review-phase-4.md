<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Auth Foundation (PUL-71) — Phase 4

- **Plan**: context/changes/pul-71-auth-foundation/plan.md
- **Scope**: Phase 4 of 6 (endpointy /api/auth/*; TDD)
- **Date**: 2026-07-17
- **Verdict**: APPROVED
- **Findings**: 0 critical, 1 warning (FIXED in-session), 1 observation (ACCEPTED)
- **Commit**: b2db64b (faza) + follow-up commit z fixem F1

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS (po fixie F1) |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

Weryfikacja na żywo: drift MATCH — `_get_firebase_app` lazy singleton z lockiem (zero inicjalizacji przy imporcie), `verify_password_rest` (Identity Toolkit REST, timeout 10s, typowane wyjątki), pełna mapa błędów (INVALID_LOGIN_CREDENTIALS/EMAIL_NOT_FOUND/INVALID_PASSWORD/USER_DISABLED → wspólny 401 anty-enumeracyjny; TOO_MANY_ATTEMPTS_TRIED_LATER → 429 bez Retry-After; timeout/5xx/nieznany kod → 503; prefiksowe dopasowanie kodów bo Firebase dokleja sufiksy), 409 na zajęty email, BQ-fail niekrytyczny (samonaprawa w loginie), `/me` z samego JWT, logout 204. Pierwszy APIRouter w repo wpięty w create_app. TDD: 3 przebiegi RED→GREEN (register / login / me+logout), 22 testy API. Kryteria: pełna suita 531 passed, ruff/mypy czyste; manual 4.3 wykonany na realnym Firebase+BQ — pełny curl loop (200+cookie/409/401/204/401), wiersz users z created_at i bumpniętym last_login_at, cleanup (Firebase user + wiersz BQ usunięte).

## Findings

### F1 — synchroniczne wywołania sieciowe blokowały event loop

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/auth.py — endpointy register/login (były `async def`)
- **Detail**: `async def` + sync httpx.post (timeout 10s) i sync Admin SDK — pojedynczy wolny strzał do Firebase zamrażał cały event loop (wszystkie endpointy). Reszta repo ma async def + sync BQ, ale przy ~100ms latencjach; tu timeout dopuszcza 10s.
- **Fix**: register/login/logout/me zamienione na `def` — FastAPI wykonuje je w threadpoolu. Zero zmian logiki; 22 testy pinują zachowanie.
- **Decision**: FIXED (in-session)

### F2 — nazwane instancje limiterów zamiast wywołania fabryki

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Plan Adherence
- **Location**: src/auth.py — _register_rate_limiter / _login_rate_limiter
- **Detail**: Plan zapisał `rate_limit(5)`/`rate_limit(10)`; implementacja używa nazwanych instancji RateLimiter (limity identyczne 5/10) z dedykowanymi dependency — instancje są resetowalne między testami API. Fabryka rate_limit pozostaje w API modułu (testowana).
- **Fix**: Nic — świadoma adaptacja dla testowalności.
- **Decision**: ACCEPTED

## Note

Status change.md pozostaje `implementing` — phase-scoped review; `impl_reviewed` ustawi pełny przegląd po fazie 6.
