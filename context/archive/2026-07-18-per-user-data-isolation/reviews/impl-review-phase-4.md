<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Per-User Data Isolation (PUL-74) — Phase 4

- **Plan**: context/changes/per-user-data-isolation/plan.md
- **Scope**: Phase 4 of 5 (commit 11a5784)
- **Date**: 2026-07-19
- **Verdict**: APPROVED (z jednym WARNING naprawionym in-session)
- **Findings**: 0 critical, 1 warning, 4 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS (3/3 MATCH; 9/9 speców przełączonych; spot-check izolacji obecny) |
| Scope Discipline | PASS (4 extras — wszystkie wymuszone faktami nieprzewidzianymi w planie, każdy udokumentowany ticketem) |
| Safety & Quality | WARNING (F1, fixed in-session) |
| Architecture | PASS |
| Pattern Consistency | PASS (zero waitForTimeout; lokatory role/label; unikalne e-maile) |
| Success Criteria | PASS (85+1 e2e, 588+1 whole; headed run potwierdzony) |

## Findings

### F1 — Test relogin sentiment nie ćwiczył resetu w doLogout()

- **Severity**: ⚠️ WARNING · **Impact**: 🔎 MEDIUM
- **Location**: tests/e2e/test_watchlist_sentiment.py:43-60
- **Detail**: Dwie warstwy maskujące: (pre-istniejące) login zaczynał się od `page.goto` → pełna nawigacja resetowała stan JS niezależnie od doLogout(); (nowe) re-dodanie PKO przez usera samo chowało pasek. Usunięcie resetu w doLogout() nie oblałoby żadnego testu.
- **Fix (applied)**: relogin bez `page.goto` (`_login_same_document_as_user`) + asercja ukrycia paska PRZED dodaniem tickera. Deliberate-break: usunięcie całego bloku resetu (index.html:1257-1259) → test czerwony; przywrócenie → zielony. Uwaga: flag-reset i hide-reset są wzajemnie redundantne (usunięcie jednej połówki kompensuje druga) — to celowa defense-in-depth.
- **Decision**: FIXED (+ deliberate-break proof)

### F2 — Patch login-rate-limitera wycieka do unit-testów (by design)

- **Severity**: ℹ️ OBSERVATION — sesyjny fixture żyje do końca pełnego przebiegu; dziś nieszkodliwe (żaden unit test nie pokrywa wiringu `_login_rate_dep`), ale przyszły test „11. login → 429" będzie po cichu zneutralizowany po e2e. Wzorzec wycieku patchy `src.api.*` jest pre-istniejący i obchodzony w test_auth_api.py:20-25. **Decision**: ACKNOWLEDGED (pułapka opisana w komentarzu conftest)

### F3 — Stały uid admina współdzieli stan fake-BQ przez cały suite

- **Severity**: ℹ️ OBSERVATION — dziś odporne (idempotentny add, asercje nie liczą wierszy); przyszłe asercje liczące wiersze watchlisty admina będą order-dependent. Komentarz przy `_watchlist_store` zaktualizowany. **Decision**: ACKNOWLEDGED

### F4 — Stały e-mail w teście deep-linku

- **Severity**: ℹ️ OBSERVATION — `e2e-deeplink@example.com` zamiast unikalnego. **Decision**: FIXED (e2e_unique_email())

### F5 — test_x_post_history asertuje etykiety nav per-user dla API-key admina

- **Severity**: ℹ️ OBSERVATION — asercja jest visibility-agnostyczna (suite zielony przy schowanych przyciskach), ale koduje w teście obecność etykiet w DOM; przy przyszłym usuwaniu przycisków z DOM (wzorzec injectAdminOnlyChrome) ten test pierwszy pęknie. **Decision**: ACKNOWLEDGED

## Coverage notes (agent drugi)

- url_routing: utrata nogi portfolio w sekwencji wymuszona przez PUL-84; deep-link per-user pokryty 2× (JWT) + nowy test regresyjny fallbacku API-key = netto zysk pokrycia.
- my_wallet reload: dowód trwałości teraz server-side (BQ keyed by uid) — silniejszy niż localStorage.
- Skip `tab=calendar`: powód zweryfikowany against `_writeUrl` guard; wraca z PUL-84.
