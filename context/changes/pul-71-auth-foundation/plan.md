# Auth Foundation (PUL-71) — Implementation Plan

## Overview

Fundament uwierzytelniania: Firebase Auth (Email/Password) do zarządzania hasłami, JWT w HttpOnly cookie jako sesja, tabela `users` w BigQuery, endpointy `/api/auth/{register,login,logout,me}` jako pierwszy `APIRouter` w repo, własny rate limiter in-memory. Istniejący API-key auth działa bez zmian. Bez UI (PUL-72), bez guest mode (PUL-73), bez izolacji danych (PUL-74).

## Current State Analysis

Pełny obraz w `research.md`. Skrót:

- Auth = 3 dependency w `src/api.py:94-116`: `_get_role` (401, porównanie z `ADMIN_API_KEY`/`USER_API_KEY`), `_require_admin` (403), `_get_client_id` (400 bez nagłówka). Zero middleware auth, zero cookies/JWT/rate-limitingu w całym repo (zweryfikowane ast-grep + grep).
- 729-liniowy `create_app()` bez routerów; prefiks `/api/*` = nowsza konwencja.
- Tabele BQ przez „triplet" (schema const + `create_*_if_not_exists` + `ensure_*_schema_current`); wartości wyłącznie `ScalarQueryParameter`; migrator dodaje tylko kolumny NULLABLE.
- Tożsamość dziś: anonimowy UUID z `localStorage` jako `X-Client-Id`; kolumny `client_id` (watchlist) / `user_id` (portfolio) niosą tę samą wartość.
- E2E `live_server_url` (`tests/e2e/conftest.py:338-411`) mockuje 30 funkcji `src.api.*` — każda nowa funkcja BQ musi dołączyć.
- Deploy: `deploy.yml:90` `--set-secrets` (replace semantics); sekrety w Secret Managerze tworzone ręcznie.
- Prereq zrobiony: Firebase podpięty (Blaze, subtype FIREBASE_AUTH), provider Email/Password włączony, SA `firebase-adminsdk-fbsvc@` z kluczem JSON poza repo.

## Desired End State

- `POST /api/auth/register` (walidacja → Firebase `create_user` → INSERT do BQ `users` → JWT cookie) — 422 na złe hasło/email, 409 na zajęty email, 429 po 5 req/IP/min.
- `POST /api/auth/login` (Identity Toolkit REST `signInWithPassword` → MERGE upsert `users` (samonaprawa + `last_login_at`) → świeże JWT cookie) — 401 na złe dane, 429 po 10 req/IP/min.
- `POST /api/auth/logout` czyści cookie; `GET /api/auth/me` zwraca `{user_id, email}` z samego JWT (bez BQ).
- Chronione endpointy akceptują **albo** ważny JWT cookie (rola `user`, client_id = Firebase UID), **albo** nagłówki API-key jak dotąd; żadne z istniejących zachowań nie zmienione.
- Cookie: HttpOnly, SameSite=Lax, Secure na Cloud Run, HS256 z `JWT_SECRET`, 7 dni, sliding refresh.

Weryfikacja: sukces-kryteria per faza + pełna suita `uv run pytest` + round-trip `scripts/test_bq_users.py` na realnym BQ.

### Key Discoveries:

- Rozszerzenie `_get_role`/`_get_client_id` nie dotyka żadnego z ~30 call site'ów (`research.md`, ast-grep: 18/3/12 użyć `Depends`)
- Szablon tabeli: watchlist `db/bigquery.py:460-488`; MERGE: `upsert_user_portfolio_position` `db/bigquery.py:523-574`
- Kolumny REQUIRED muszą być w initial create — migrator tylko NULLABLE (`db/bigquery.py:1815-1816`)
- `httpx` już jest w deps — REST do Identity Toolkit bez nowych bibliotek HTTP; testy mockują przez `respx` (już w dev-deps)
- Lekcja GCP: `load_dotenv()` przed importami + guard `with_quota_project`; firebase-admin czyta credentials z env — analogiczna dyscyplina inicjalizacji (lazy singleton)

## What We're NOT Doing

- OAuth / Google sign-in (decyzja epiku; architektura provider-agnostic — osobny ticket przy PUL-72)
- UI logowania/rejestracji (PUL-72), guest mode i `/api/public/*` (PUL-73), izolacja danych istniejących tabel (PUL-74)
- Email verification, password reset, role poza admin(API-key)/user
- JWT cookie dla ścieżki API-key — API-key zostaje czysto nagłówkowy (decyzja Q3)
- Migracja istniejących danych (watchlist/portfolio) na Firebase UID
- Redis/globalny rate limiting — licznik per-instancja (max 2 instancje ⇒ świadomie akceptowane do 2× limitu)
- Zmiana notki GDPR w UI (`static/index.html:815`) — razem z UI w PUL-72

## Implementation Approach

Backend-only, addytywnie. Nowy moduł `src/auth.py` (pierwszy `APIRouter`) trzyma całą logikę: walidacje, JWT, rate limiter, klientów Firebase. `db/bigquery.py` dostaje triplet `users` + 2 funkcje CRUD. `src/api.py` zmienia się minimalnie: `include_router`, rozszerzenie 2 dependency, startup hook. Logowanie idzie przez Identity Toolkit REST (decyzja Q1) — hasło weryfikuje Firebase, my nie trzymamy żadnych hashy. Rejestracja przez Admin SDK (`firebase_admin.auth.create_user`) — pewniejsza ścieżka i czytelne wyjątki (`EmailAlreadyExistsError` → 409).

## Critical Implementation Details

- **IP za proxy Cloud Run**: `request.client.host` to IP proxy, nie klienta. Rate limiter musi czytać pierwszy element `X-Forwarded-For` z fallbackiem na `request.client.host` (lokalny dev). Bez tego wszyscy użytkownicy prod zlewają się w jeden limit.
- **Secure cookie**: Cloud Run ustawia env `K_SERVICE` automatycznie — `secure=bool(os.environ.get("K_SERVICE"))` daje Secure=True na prod i False lokalnie bez nowego env vara.
- **Sliding refresh w dependency**: FastAPI dependency może przyjąć `response: Response` i wywołać `set_cookie` — rozszerzony `_get_role` re-emituje cookie, gdy token starszy niż 24h. To jedyny nieoczywisty mechanizm „refresh on activity" bez middleware.
- **`FIREBASE_SERVICE_ACCOUNT_JSON` to treść JSON, nie ścieżka** — `json.loads(...)` → `credentials.Certificate(dict)`. Lazy singleton z lockiem wzorem `_get_client()` w `db/bigquery.py:85-105`; inicjalizacja NIE przy imporcie (E2E/unit testy nie mają tego env vara).
- **Firebase może być wolny/niedostępny** — wywołania Admin SDK i REST w `try/except` → 503 z czytelnym komunikatem (nie 500 ze stack trace); timeout na REST 10s.

## Phase 1: Zależności + konfiguracja

### Overview
Dodaje biblioteki i plumbing env vars; zero logiki.

### Changes Required:

#### 1. Zależności
**File**: `pyproject.toml` (przez `uv add firebase-admin pyjwt email-validator`)
**Intent**: Trzy nowe zależności runtime. `httpx`/`respx` już są.
**Contract**: `uv.lock` zaktualizowany; `uv run python -c "import firebase_admin, jwt, email_validator"` przechodzi.

#### 2. Env vars
**File**: `.env.example`, `api_main.py`
**Intent**: Udokumentować `JWT_SECRET`, `FIREBASE_SERVICE_ACCOUNT_JSON`, `FIREBASE_WEB_API_KEY`; dopisać `JWT_SECRET` do startup guarda w `api_main.py:12-14` (Firebase vars celowo poza guardem — endpointy auth mają zwracać 503 gdy brak, ale API-key path musi startować bez Firebase).
**Contract**: `api_main.py` rzuca `RuntimeError` bez `JWT_SECRET`; `.env.example` listuje wszystkie trzy z komentarzem skąd je wziąć (klucz SA: `C:\Users\PC KOMPUTER\.secrets\puls-gpw-firebase-adminsdk.json`; Web API key: config Firebase projektu).

### Success Criteria:

#### Automated Verification:
- `uv run python -c "import firebase_admin, jwt, email_validator"` przechodzi
- `uv run pytest` — istniejąca suita zielona (brak regresji po dodaniu deps)
- `uv run ruff check .` i `uv run mypy .` zielone

#### Manual Verification:
- Lokalny `.env` uzupełniony o 3 nowe wartości; `uv run python api_main.py` startuje

---

## Phase 2: Tabela BigQuery `users` + round-trip

### Overview
Triplet + 2 funkcje CRUD + skrypt round-trip; wpięcie w startup hook.

### Changes Required:

#### 1. Triplet `users`
**File**: `db/bigquery.py`
**Intent**: Schema + create + ensure wzorem watchlist (`:460-488`).
**Contract**: `_USERS_SCHEMA`: `user_id` STRING REQUIRED, `email` STRING REQUIRED, `created_at` TIMESTAMP REQUIRED, `last_login_at` TIMESTAMP NULLABLE. REQUIRED w initial create (migrator tylko NULLABLE). Funkcje: `create_users_table_if_not_exists()`, `ensure_users_schema_current()`.

#### 2. CRUD
**File**: `db/bigquery.py`
**Intent**: `insert_user(user_id, email)` — INSERT z `created_at = CURRENT_TIMESTAMP()` (register); `upsert_user_login(user_id, email)` — MERGE: gdy brak wiersza INSERT (samonaprawa po partial failu, decyzja Q6), zawsze UPDATE `last_login_at = CURRENT_TIMESTAMP()` (login).
**Contract**: Wzorzec MERGE z `upsert_user_portfolio_position` (`:523-574`); wszystkie wartości przez `ScalarQueryParameter`. Nazwy kolumn nie kolidują z reserved keywords BQ (sprawdzone: user_id/email/created_at/last_login_at — czyste).

#### 3. Startup hook + round-trip
**File**: `src/api.py:245-254`, `scripts/test_bq_users.py` (nowy)
**Intent**: create+ensure `users` na starcie (konwencja repo); nowy sibling-skrypt round-trip (decyzja Q8): create → insert → upsert_login → asercje → cleanup w `finally`.
**Contract**: Skrypt wzorem `scripts/test_bq_user_portfolios.py`; `load_dotenv()` pierwsze; woła też `ensure_users_schema_current()` (lekcja: create jest no-opem na istniejącej tabeli).

### Success Criteria:

#### Automated Verification:
- `uv run pytest tests/test_bigquery.py` — nowe testy jednostkowe CRUD (mock `_get_client`) zielone, w tym tani test regresyjny na stringi SQL
- `uv run pytest` — całość zielona

#### Manual Verification:
- `uv run python scripts/test_bq_users.py` na realnym BQ przechodzi (obowiązkowe wg lessons — mocki nie weryfikują składni SQL)

---

## Phase 3: Rdzeń auth — walidacje, JWT, rate limiter (TDD)

### Overview
Czysta logika w nowym `src/auth.py`, bez endpointów. Fazy 3-5 są TDD-owalne.

### Changes Required:

#### 1. Walidacje
**File**: `src/auth.py` (nowy)
**Intent**: Pydantic `RegisterIn`/`LoginIn` z `field_validator`: email przez `email_validator` (strip whitespace przed walidacją), hasło 8-128 znaków + ≥1 litera + ≥1 cyfra. Naruszenia → naturalne 422 FastAPI z czytelnym komunikatem; nic niepoprawnego nie wychodzi do Firebase.
**Contract**: `RegisterIn(email: str, password: str)`; walidator hasła odrzuca też >128 przed jakimkolwiek hashowaniem (DoS guard z ticketa).

#### 2. JWT
**File**: `src/auth.py`
**Intent**: `create_session_token(user_id, email, auth_type)` / `decode_session_token(token)` — pyjwt HS256, exp 7 dni, payload `{user_id, email, auth_type, iat, exp}`; `set_session_cookie(response, token)` / `clear_session_cookie(response)` z HttpOnly, SameSite=Lax, `secure=bool(os.environ.get("K_SERVICE"))`.
**Contract**: Nazwa cookie: `session`. `decode_session_token` zwraca payload lub `None` (nieważny/wygasły — nigdy nie rzuca do handlera). `auth_type` ∈ {"firebase", "api_key"} — pole jest w kontrakcie payloadu od razu (grunt pod przyszłość), choć w PUL-71 wystawiamy tylko "firebase".

#### 3. Rate limiter
**File**: `src/auth.py`
**Intent**: Własny licznik in-memory (decyzja Q4): `dict[str, deque[float]]` + lock, okno 60s; fabryka dependency `rate_limit(max_per_minute)` → 429 z nagłówkiem `Retry-After` (sekundy do zwolnienia najstarszego slotu).
**Contract**: IP z pierwszego elementu `X-Forwarded-For`, fallback `request.client.host`. Wstrzykiwalny zegar (`time_fn`) dla testów. Stan per-instancja — świadomy trade-off (max 2 instancje).

### Success Criteria:

#### Automated Verification:
- `uv run pytest tests/test_auth.py` — walidacje (8/128/litera/cyfra/strip/email), JWT (roundtrip, zły podpis, wygasły), rate limiter (limit, okno, Retry-After, X-Forwarded-For) zielone
- `uv run ruff check .` i `uv run mypy .` zielone

#### Manual Verification:
- (brak — czysta logika pokryta testami)

---

## Phase 4: Endpointy `/api/auth/*` (TDD)

### Overview
Router + integracja Firebase; testy z mockami Admin SDK i respx dla REST.

### Changes Required:

#### 1. Klienci Firebase
**File**: `src/auth.py`
**Intent**: Lazy singleton `_get_firebase_app()` (json.loads env → `credentials.Certificate`; lock wzorem `db/bigquery.py:85-105`); `verify_password_rest(email, password)` — POST `https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}` przez httpx, timeout 10s, zwraca `(local_id, email)` lub podnosi typowany wyjątek na złe dane (401) / błąd usługi (503).
**Contract**: Brak env vara / błąd inicjalizacji → 503 „auth temporarily unavailable", nie 500. Żadna inicjalizacja przy imporcie modułu.

#### 2. Router
**File**: `src/auth.py`, `src/api.py`
**Intent**: `router = APIRouter(prefix="/api/auth")` — pierwszy router w repo (decyzja Q2); `app.include_router(...)` w `create_app()`. Endpointy:
- `POST /register` (`rate_limit(5)`): walidacja → `auth.create_user` → `insert_user` (błąd BQ tylko logowany — samonaprawa w loginie, Q6) → cookie; `EmailAlreadyExistsError` → 409 „Email jest już zarejestrowany" (Q7)
- `POST /login` (`rate_limit(10)`): `verify_password_rest` → `upsert_user_login` (błąd BQ logowany, nie blokuje) → świeże cookie; złe dane → 401 (jednolity komunikat, bez rozróżniania email/hasło)
- `POST /logout`: `clear_session_cookie` → 204
- `GET /api/auth/me`: dekoduje cookie, zwraca `{user_id, email}`; bez BQ; 401 gdy brak/nieważny
**Contract**: Register i login zwracają 200 z `{user_id, email}` + `Set-Cookie`. Odpowiedzi błędów w kształcie FastAPI `{"detail": ...}`.

### Success Criteria:

#### Automated Verification:
- `uv run pytest tests/test_auth_api.py` — TestClient: register happy path (mock create_user + insert_user, cookie HttpOnly w odpowiedzi), 422 (za krótkie/za długie/bez cyfry/zły email), 409, 429 z Retry-After (6. request), login happy/401/503 (respx), logout czyści cookie, me z cookie/bez
- `uv run pytest` — całość zielona

#### Manual Verification:
- `curl -i` lokalnie: register → login → me (cookie z pliku) → logout; wpis pojawia się w BQ `users`, `last_login_at` aktualizuje się po login

---

## Phase 5: Rozszerzenie auth seam (TDD)

### Overview
Chronione endpointy akceptują JWT cookie obok API-key. Zero zmian na call site'ach.

### Changes Required:

#### 1. `_get_role` + `_get_client_id`
**File**: `src/api.py:94-116`
**Intent**: Kolejność z ticketa: (1) ważny JWT cookie → rola `user` (admin pozostaje wyłącznie API-key), sliding refresh — re-emisja cookie gdy token starszy niż 24h (przez `response: Response` w dependency); (2) `X-API-Key` → istniejąca logika bez zmian; (3) nic → 401. `_get_client_id`: JWT → `user_id` z tokenu (Firebase UID staje się client_id — grunt pod PUL-74); brak JWT → nagłówek `X-Client-Id` jak dziś (400 gdy brak).
**Contract**: Sygnatury dependency niezmienione dla call site'ów. Nieważny/wygasły cookie = brak cookie (fallthrough na nagłówki, nie 401 wprost — pozwala API-key działać nawet ze starym cookie w przeglądarce).

### Success Criteria:

#### Automated Verification:
- `uv run pytest tests/test_api.py` — nowe testy: dostęp do `/announcements` z samym cookie (200, rola user), z samym API-key (bez regresji — istniejące testy zielone bez modyfikacji), z niczym (401), wygasły cookie + ważny API-key (200), `/admin/*` z cookie (403), watchlist z cookie używa user_id z JWT
- `uv run pytest` — pełna suita zielona

#### Manual Verification:
- Lokalnie: po login przez curl, `GET /announcements` z samym cookie działa; z samym `X-API-Key` działa jak dotąd

---

## Phase 6: E2E conftest + deploy + docs

### Overview
Domknięcie: mocki E2E, sekrety deploy, dokumentacja.

### Changes Required:

#### 1. E2E conftest
**File**: `tests/e2e/conftest.py:338-411`
**Intent**: Dopisać do `_patches`: `create_users_table_if_not_exists`, `ensure_users_schema_current`, `insert_user`, `upsert_user_login` (jako `src.api.*` lub `src.auth.*` wg importów) + mock `_get_firebase_app`/`verify_password_rest`; env `JWT_SECRET` w fixture. Bez tego E2E uderzy w prawdziwe BQ/Firebase (lekcja conftest-bq-mocking).
**Contract**: Istniejące 47 testów E2E zielone bez zmian zachowania; smoke E2E: login API-key działa jak dotąd.

#### 2. Deploy + sekrety
**File**: `.github/workflows/deploy.yml:90-91`, `context/foundation/infra.md`
**Intent**: Dopisać `JWT_SECRET=jwt-secret:latest,FIREBASE_SERVICE_ACCOUNT_JSON=firebase-service-account:latest` do `--set-secrets` (replace semantics — cała lista razem!); `FIREBASE_WEB_API_KEY` do `--set-env-vars` (klucz publiczny, nie sekret). Zaktualizować tabelę sekretów w `infra.md`.
**Contract**: MANUAL (human-only, przed merge): utworzyć sekrety `jwt-secret` (wygenerowany, np. `openssl rand -hex 32`) i `firebase-service-account` w Secret Managerze projektu puls-gpw + upewnić się, że `puls-gpw-runner@` ma `secretmanager.secretAccessor`.

### Success Criteria:

#### Automated Verification:
- `uv run pytest tests/e2e` — pełna suita E2E zielona
- `uv run pytest` + `uv run ruff check .` + `uv run mypy .` zielone

#### Manual Verification:
- Sekrety utworzone w Secret Managerze (human) — checkpoint przed merge
- Po merge + deploy CI: `curl https://<prod>/health` OK; register/login/me na prod działa; istniejący frontend (API-key) działa bez zmian

---

## Testing Strategy

### Unit Tests:
- `tests/test_auth.py` (Faza 3): walidacje brzegowe (7/8/128/129 znaków, bez litery, bez cyfry, whitespace-strip, złe emaile), JWT roundtrip/tamper/expiry, rate limiter (okno, wielu klientów, Retry-After, X-Forwarded-For parsing)
- `tests/test_auth_api.py` (Faza 4): pełna macierz endpointów z mockami (TestClient obsługuje cookies natywnie)
- `tests/test_bigquery.py` (Faza 2): CRUD users na mocku klienta + asercje na stringi SQL
- `tests/test_api.py` (Faza 5): macierz cookie/API-key/nic × endpoint user/admin

### Integration Tests:
- `scripts/test_bq_users.py` — round-trip na realnym BQ (obowiązkowy, lessons)
- E2E: istniejąca suita jako regresja; dedykowane E2E scenariusze logowania UI → PUL-72

### Manual Testing Steps:
1. Lokalnie curl: register → 200 + cookie; ponowny register → 409; hasło „krotkie1" → 422; 6× register w minutę → 429 z Retry-After
2. login → me → logout → me (401)
3. `/announcements` z cookie (200) i z API-key (200, bez zmian); JWT cookie niewidoczny z JS (HttpOnly)
4. BQ: wiersz w `users` z poprawnym `created_at`/`last_login_at`

## Performance Considerations

Rate limiter O(1) amortyzowane per request (deque prune). JWT dekodowanie lokalne (HS256) — brak wywołań sieciowych na chronionych endpointach; Firebase dotykany tylko w register/login. `/api/auth/me` bez BQ (wymóg ticketa).

## Migration Notes

Brak migracji danych. Istniejący anonimowy UUID (X-Client-Id) i Firebase UID współistnieją jako wartości `client_id`/`user_id`; ujednolicenie = PUL-74. Rollback: feature jest addytywny — revert commita przywraca stan sprzed (tabela `users` może zostać, nic z niej nie czyta).

## References

- Research: `context/changes/pul-71-auth-foundation/research.md`
- Ticket: Linear PUL-71 / GitHub #127; epic PUL-70
- Szablon tabeli: `db/bigquery.py:460-488`; MERGE: `db/bigquery.py:523-574`; auth seam: `src/api.py:94-116`; E2E mocki: `tests/e2e/conftest.py:338-411`; deploy: `.github/workflows/deploy.yml:81-97`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Zależności + konfiguracja

#### Automated
- [ ] 1.1 Import trzech nowych bibliotek przechodzi
- [ ] 1.2 Istniejąca suita pytest zielona po dodaniu deps
- [ ] 1.3 ruff + mypy zielone

#### Manual
- [ ] 1.4 Lokalny `.env` uzupełniony; `api_main.py` startuje

### Phase 2: Tabela BigQuery `users` + round-trip

#### Automated
- [ ] 2.1 Testy jednostkowe CRUD users (mock `_get_client`) zielone
- [ ] 2.2 Pełna suita pytest zielona

#### Manual
- [ ] 2.3 `scripts/test_bq_users.py` na realnym BQ przechodzi

### Phase 3: Rdzeń auth (TDD)

#### Automated
- [ ] 3.1 `tests/test_auth.py` — walidacje, JWT, rate limiter zielone
- [ ] 3.2 ruff + mypy zielone

### Phase 4: Endpointy `/api/auth/*` (TDD)

#### Automated
- [ ] 4.1 `tests/test_auth_api.py` — pełna macierz endpointów zielona
- [ ] 4.2 Pełna suita pytest zielona

#### Manual
- [ ] 4.3 curl register→login→me→logout lokalnie; wiersz widoczny w BQ `users`

### Phase 5: Rozszerzenie auth seam (TDD)

#### Automated
- [ ] 5.1 Macierz cookie/API-key/nic w `tests/test_api.py` zielona; istniejące testy bez modyfikacji
- [ ] 5.2 Pełna suita pytest zielona

#### Manual
- [ ] 5.3 `/announcements` działa z samym cookie i z samym API-key

### Phase 6: E2E conftest + deploy + docs

#### Automated
- [ ] 6.1 Pełna suita E2E zielona
- [ ] 6.2 pytest + ruff + mypy zielone

#### Manual
- [ ] 6.3 Sekrety `jwt-secret` + `firebase-service-account` utworzone w Secret Managerze (human-only)
- [ ] 6.4 Po deploy: prod register/login/me działa; frontend API-key bez regresji
