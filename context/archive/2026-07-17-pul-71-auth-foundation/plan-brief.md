# Auth Foundation (PUL-71) — Plan Brief

> Full plan: `context/changes/pul-71-auth-foundation/plan.md`
> Research: `context/changes/pul-71-auth-foundation/research.md`

## What & Why

Zastępujemy fundament pod „jeden współdzielony klucz API + anonimowy UUID przeglądarki" prawdziwymi kontami użytkowników: Firebase Auth trzyma hasła, nasz backend wystawia sesję jako JWT w HttpOnly cookie, a BigQuery dostaje tabelę `users`. To pierwszy z czterech pod-ticketów epiku PUL-70 (rejestracja → UI → guest mode → izolacja danych); bez niego żaden kolejny nie ruszy.

## Starting Point

Auth dziś to 3 dependency FastAPI porównujące nagłówek `X-API-Key` z dwoma env varami, a „tożsamość" to losowy UUID w localStorage wysyłany jako `X-Client-Id`. W repo nie ma ani jednej linii kodu cookies, JWT ani rate limitingu. Firebase jest już podpięty do projektu GCP (prereq zrobiony ręcznie 2026-07-17: provider Email/Password + service account z kluczem poza repo).

## Desired End State

Działa `POST /api/auth/register` i `login` (walidacja hasła 8-128 + litera+cyfra, 409 na zajęty email, 429 po przekroczeniu 5/10 req/IP/min), `logout` i `me`. Zalogowany użytkownik Firebase korzysta z chronionych endpointów samym cookie; dotychczasowi użytkownicy API-key nie zauważają żadnej zmiany. UI nadal stary — ekran logowania i landing to PUL-72.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Sesja | JWT HttpOnly cookie, HS256, 7 dni, sliding refresh | Stateless, zgodne z FastAPI, bez tabeli sesji | Ticket |
| Hasła | Firebase Auth (Email/Password, bez Identity Platform) | Darmowe, zero krypto do utrzymania | Ticket |
| Weryfikacja hasła przy loginie | Backend → Identity Toolkit REST `signInWithPassword` | Zero klienckiego SDK — spójne z vanilla-JS frontendem; rate limiting w pełni po naszej stronie | Plan |
| Google sign-in | Poza zakresem; architektura provider-agnostic | Epic wyklucza OAuth; Firebase doda provider bez przebudowy — osobny ticket przy PUL-72 | Plan |
| Organizacja kodu | Nowy `src/auth.py` + pierwszy `APIRouter` w repo | api.py ma 729 linii; auth testowalny w izolacji | Plan |
| Ścieżka API-key | Bez cookie — czysto nagłówkowa jak dziś | Dosłowne „keeps working unchanged", minimalne pole regresji | Plan |
| Rate limiter | Własny licznik in-memory (deque per IP) | Zero zależności, trywialne testy; per-instancja to świadomy trade-off (max 2 instancje) | Plan |
| Partial fail register (Firebase OK, BQ padło) | Sukces + samonaprawa MERGE przy loginie | User nigdy nie zablokowany; wzorzec MERGE już w repo | Plan |
| Zajęty email | 409 z czytelnym komunikatem | Uczciwy UX; enumerację dławi rate limit | Plan |
| Round-trip BQ | Nowy `scripts/test_bq_users.py` | Konwencja sibling-skryptów; mocki nie łapią składni SQL (lekcja PUL-29) | Research |
| client_id dla userów Firebase | `user_id` z JWT zastępuje `X-Client-Id` | Grunt pod izolację danych w PUL-74 bez migracji | Plan |

## Scope

**In scope:** deps (firebase-admin, pyjwt, email-validator) · tabela BQ `users` + CRUD + round-trip · `src/auth.py` (walidacje, JWT, rate limiter, klienci Firebase) · router `/api/auth/*` · rozszerzenie `_get_role`/`_get_client_id` o cookie · mocki E2E · deploy secrets + docs

**Out of scope:** UI (PUL-72) · guest mode i `/api/public/*` (PUL-73) · izolacja istniejących danych (PUL-74) · OAuth/Google · email verification/reset hasła · Redis · migracja danych · notka GDPR

## Architecture / Approach

`src/auth.py` (nowy): Pydantic walidacje → Firebase (Admin SDK dla create_user, REST dla signIn) → `db/bigquery.py` (`users` triplet + insert/upsert MERGE) → JWT cookie. `src/api.py` zmienia się minimalnie: `include_router` + rozszerzenie 2 dependency (kolejność: ważny cookie → nagłówki → 401), bez dotykania ~30 call site'ów. Wszystko addytywne — rollback = revert commita.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Zależności + config | 3 biblioteki, env vars, startup guard | konflikt wersji deps (niski) |
| 2. Tabela `users` + round-trip | triplet + CRUD + `test_bq_users.py` | składnia SQL niewidoczna w mockach → round-trip obowiązkowy |
| 3. Rdzeń auth (TDD) | walidacje, JWT, rate limiter | parsing X-Forwarded-For za proxy Cloud Run |
| 4. Endpointy `/api/auth/*` (TDD) | register/login/logout/me + Firebase | obsługa niedostępności Firebase (503, nie 500) |
| 5. Auth seam (TDD) | cookie akceptowany obok API-key | regresja istniejących endpointów — istniejące testy muszą przejść bez modyfikacji |
| 6. E2E + deploy + docs | mocki conftest, `--set-secrets`, infra.md | replace-semantyka `--set-secrets`; sekrety = krok human-only |

**Prerequisites:** Firebase skonfigurowany ✓ (2026-07-17) · klucz SA w `C:\Users\PC KOMPUTER\.secrets\` ✓ · przed merge: sekrety w Secret Managerze (human)
**Estimated effort:** ~2-3 sesje; fazy 3-5 przez `/10x-tdd`, 1-2 i 6 przez `/10x-implement`

## Open Risks & Assumptions

- Rate limiter per-instancja: przy max 2 instancjach Cloud Run efektywny limit to do 2× nominalnego — zaakceptowane
- Zakładamy, że `FIREBASE_WEB_API_KEY` (klucz publiczny web) wystarcza do REST signInWithPassword bez ograniczeń API-key restrictions — do potwierdzenia w fazie 4 na realnym projekcie
- Kolizja nazw `client_id`/`user_id` w BQ pozostaje długiem do PUL-74

## Success Criteria (Summary)

- Nowy użytkownik: register → cookie → korzysta z aplikacji; złe hasło/email → czytelne 422; 6. próba w minutę → 429
- Istniejący użytkownik API-key: zero zauważalnych zmian (testy przechodzą bez modyfikacji)
- Cookie niewidoczny z JS (HttpOnly), `users` w BQ z poprawnymi timestampami
