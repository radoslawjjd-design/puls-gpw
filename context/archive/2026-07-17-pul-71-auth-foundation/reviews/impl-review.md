<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Auth Foundation (PUL-71) — Full Plan

- **Plan**: context/changes/pul-71-auth-foundation/plan.md
- **Scope**: Phases 1-6 of 6 (full-plan sweep; phases 1-5 had phase-scoped APPROVED reviews)
- **Date**: 2026-07-17
- **Verdict**: APPROVED
- **Findings**: 0 critical, 2 warnings, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

Drift sweep: 0 DRIFT, 0 MISSING across all 6 phases; 2 EXTRA, oba udokumentowane (sweep rate limitera = fix z review fazy 3; dodatkowy test sliding refresh). Granice "What We're NOT Doing" w całości dotrzymane. Kryteria automatyczne zweryfikowane na żywo: importy OK, pełna suita 537 passed (w tym 65 E2E), ruff 36 / mypy 71 = dokładnie baseline mastera (adaptacja "zero nowych błędów" z change.md). Manualne 1.4/2.3/4.3/5.3/6.3 wykonane z dowodami w phase-review'ach; 6.4 pending (post-deploy, z natury).

Zweryfikowane jako czyste: parametryzacja SQL (ScalarQueryParameter wszędzie), sekrety przez Secret Manager (inline tylko publiczny FIREBASE_WEB_API_KEY — celowe), 3 warstwy guardów pustego JWT_SECRET, flagi cookie (HttpOnly/SameSite=Lax/Secure na K_SERVICE), lazy singleton Firebase (double-checked lock, retry po failu), locking rate limitera bez TOCTOU, hot path bez sieci/BQ, anti-spoofing XFF (ostatni element, pinowane testem; uwaga na przyszłość: zewnętrzny LB przed serwisem → przejście na [-2]).

Zaakceptowane trade-offy (bez akcji): 409 przy register = oracle enumeracji emaili (standard, rate-limited 5/min/IP; przy password reset w przyszłości trzymać non-enumerating); porównanie API-key przez `==` zamiast secrets.compare_digest (pre-existing, nietknięte); sweep O(n) pod lockiem przy >1000 aktywnych IP w oknie (nieistotne przy obecnej skali); CSRF oparty wyłącznie o SameSite=Lax (wystarczające póki GET-y są read-only).

## Findings

### F1 — Zniekształcone 200 z Identity Toolkit ucieka jako surowe 500 w /login

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/auth.py:261-263 (parsowanie 200-ścieżki verify_password_rest)
- **Detail**: Ścieżka sukcesu robi `resp.json()` i `data["localId"]` bez guarda — 200 z body nie-JSON (JSONDecodeError) albo bez `localId` (KeyError) propaguje z funkcji; endpoint login łapie tylko 3 typowane wyjątki → nieobsłużone 500, wbrew kontraktowi modułu "503, nigdy 500". Register ma broad catch-all, login nie.
- **Fix**: Owinąć parsowanie 200-ścieżki w `try/except (ValueError, KeyError) → AuthUnavailableError`.
- **Decision**: FIXED — guard w src/auth.py + przypadek `malformed_200` w sparametryzowanym teście 503

### F2 — Sliding refresh = sesja bez absolutnego limitu życia i bez rewokacji

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/auth.py:124-130 (+ src/api.py:110-112)
- **Detail**: Refresh re-emituje świeży 7-dniowy token z payloadu starego przy iat>24h — skradzione cookie można przesuwać w nieskończoność (replay raz dziennie); zmiana hasła / wyłączenie konta w Firebase nie unieważnia sesji (seam nie re-checkuje Firebase/BQ; logout czyści cookie tylko po stronie klienta).
- **Fix A ⭐ Recommended**: Absolutny cap — claim `login_at` w payloadzie, refresh odmawia gdy login_at starszy niż 30 dni
  - Strength: Zamyka nieskończone przesuwanie ~10 liniami przed pierwszym prod deployem.
  - Tradeoff: Re-login po 30 dniach; drobna zmiana kontraktu payloadu.
  - Confidence: HIGH — mechanizm refresh już pinowany testami.
  - Blind spot: Rewokacja per-user (denylist) nadal poza zakresem — osobny przyszły ticket.
- **Fix B**: Tylko nota decyzyjna w change.md
  - Strength: Zero kodu przed merge; ryzyko realnie niskie przy obecnej powierzchni.
  - Tradeoff: Nieskończona sesja w prod do czasu przyszłego ticketa.
  - Confidence: MED.
  - Blind spot: Łatwo zapomnieć wrócić do tematu.
- **Decision**: FIXED via Fix A — claim `login_at` (przetrwa refreshe, fallback na iat dla starych tokenów), `_SESSION_ABSOLUTE_MAX_SECONDS = 30 dni`, refresh odmawia po capie; 3 nowe testy (login_at==iat przy fresh, odmowa po 31 dniach, zachowanie oryginalnego login_at przy refreshu). Rewokacja per-user pozostaje znaną luką na przyszły ticket.

### F3 — decode nie wymaga claimów (exp/iat/user_id) — teoretyczny KeyError→500

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/auth.py:92 (+ konsumenci payload["user_id"/"email"])
- **Detail**: HS256 przypięty, ale podpisany token bez exp byłby ważny wiecznie; bez user_id/email → KeyError→500 u konsumentów. Dziś tylko posiadacz sekretu może taki zminąć — hardening, nie dziura.
- **Fix**: `options={"require": ["exp", "iat"]}` w jwt.decode + walidacja obecności user_id/email w decode_session_token (return None gdy brak).
- **Decision**: FIXED — require exp/iat + guard user_id/email w decode; nowy test na oba warianty

### F4 — Conftest E2E nie mockuje firebase_auth.create_user (przyszły register-E2E)

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria (test gap)
- **Location**: tests/e2e/conftest.py:394-402
- **Detail**: Zgodne z planem (lista mocków 1:1) i bezpieczne dziś (register-E2E dostałby 503 lokalnie, nie dotknie realnego Firebase), ale ugryzie autora pierwszego register-E2E w PUL-72.
- **Fix**: Dopisać `patch("src.auth.firebase_auth.create_user", ...)` do `_patches`.
- **Decision**: FIXED — mock zwraca SimpleNamespace(uid="e2e-firebase-uid"), spójnie z mockiem verify_password_rest

### F5 — clear_session_cookie bez flagi secure (asymetria z set)

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/auth.py:110-112
- **Detail**: Setter warunkowo daje secure, delete nie — kasowanie działa (dopasowanie po name/domain/path), czysto kosmetyczna asymetria.
- **Fix**: Przekazać `secure=bool(os.environ.get("K_SERVICE"))` też w delete_cookie.
- **Decision**: FIXED — symetria flagi secure w delete_cookie

## Triage — podsumowanie (2026-07-17)

Wszystkie 5 findingów FIXED in-session (F2 via Fix A). Po fixach: pełna suita **542 passed** (537 + 5 nowych testów), ruff 36 / mypy 71 = baseline mastera (zero nowych). Verdict pozostaje **APPROVED**.
