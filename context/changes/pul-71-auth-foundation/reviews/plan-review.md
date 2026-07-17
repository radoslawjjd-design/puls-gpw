<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Auth Foundation (PUL-71)

- **Plan**: context/changes/pul-71-auth-foundation/plan.md
- **Mode**: Deep
- **Date**: 2026-07-17
- **Verdict**: REVISE → **SOUND** (po zastosowaniu wszystkich 5 fixów)
- **Findings**: 2 critical, 1 warning, 2 observations — wszystkie FIXED

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS (po F4) |
| Blind Spots | FAIL → PASS (po F1, F3, F5) |
| Plan Completeness | WARNING → PASS (po F2) |

## Grounding

7/7 paths ✓ (src/api.py, db/bigquery.py, tests/e2e/conftest.py, deploy.yml, api_main.py, .env.example, scripts/test_bq_user_portfolios.py) · symbole ✓ (`_get_role`/`_get_client_id`/`_require_admin` wyłącznie w src/api.py — blast radius potwierdzony) · brief↔plan ✓ · contract-surfaces.md nie istnieje (pominięte). Weryfikacja celowana grepami zamiast sub-agenta: brak chronionych endpointów zwracających goły Response (sliding refresh działa), conftest ustawia env przed create_app (:340-341); kod zweryfikowany wcześniej w sesji przez 4 agentów researchu + ast-grep.

## Findings

### F1 — Rate limiter po pierwszym elemencie X-Forwarded-For jest trywialnie omijalny

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix oczywisty i wąski
- **Dimension**: Blind Spots
- **Location**: Critical Implementation Details + Faza 3
- **Detail**: Pierwszy element XFF jest kontrolowany przez klienta — spoofing omija limit (rotacja wartości) i zatruwa kubełki cudzych IP. Na Cloud Run Google Front End dopisuje realny IP klienta jako OSTATNI element. Testy z planu przeszłyby, a realna ochrona byłaby zerowa.
- **Fix**: Ostatni element XFF (`split(",")[-1].strip()`), fallback `request.client.host`; test anty-spoofingowy; manual check na prod w fazie 4; nota o przyszłym LB.
- **Decision**: FIXED — plan zaktualizowany (Critical Implementation Details + kontrakt fazy 3 + test)

### F2 — Placeholder "(brak…)" w Manual Verification fazy 3 łamie kontrakt Progress

- **Severity**: ❌ CRITICAL (mechaniczny — parser /10x-implement)
- **Impact**: 🏃 LOW
- **Dimension**: Plan Completeness
- **Location**: Faza 3 — Success Criteria
- **Detail**: Bullet-atrapa bez odpowiednika `N.M` w `## Progress`; kontrakt wymaga 1:1.
- **Fix**: Usunięty nagłówek Manual Verification + placeholder z ciała fazy 3.
- **Decision**: FIXED

### F3 — Brak mapowania błędów Firebase → kody HTTP

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — realna decyzja projektowa (anty-enumeracja)
- **Dimension**: Blind Spots
- **Location**: Faza 4 — Router (login)
- **Detail**: REST signInWithPassword zwraca INVALID_LOGIN_CREDENTIALS / EMAIL_NOT_FOUND / INVALID_PASSWORD / USER_DISABLED / TOO_MANY_ATTEMPTS_TRIED_LATER; plan mówił tylko „złe dane → 401" — implementer musiałby zgadywać.
- **Fix**: Tabela mapowania w kontrakcie fazy 4: warianty złych danych + USER_DISABLED → 401 (jeden komunikat), TOO_MANY_ATTEMPTS → 429, inne/5xx/timeout/nieznane → 503.
- **Decision**: FIXED

### F4 — JWT_SECRET musi być czytany w call-time, nie przy imporcie

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Architectural Fitness
- **Location**: Faza 3 — JWT
- **Detail**: Repo ma dwie konwencje env (import-time w db/bigquery, call-time w _get_role); import-time w auth.py wywróciłby unit testy i E2E.
- **Fix**: Jawny zapis w kontrakcie fazy 3: call-time wzorem `src/api.py:100`.
- **Decision**: FIXED

### F5 — Przyszłe endpointy zwracające obiekt Response ominą sliding refresh

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Blind Spots
- **Location**: Critical Implementation Details
- **Detail**: Dziś żaden chroniony endpoint nie zwraca gołego Response (zweryfikowane), ale przyszły endpoint zwracający własny Response po cichu pominie re-emisję cookie.
- **Fix**: Zdanie ostrzegawcze w Critical Implementation Details.
- **Decision**: FIXED
