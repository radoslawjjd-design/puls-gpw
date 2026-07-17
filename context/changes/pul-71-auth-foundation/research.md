---
date: 2026-07-17T14:02:21+02:00
researcher: Claude (Fable 5) + Radek
git_commit: b74cda7eda82c6be4078a18fcac57c72c63fa032
branch: pul-71-auth-foundation
repository: puls-gpw
topic: "Auth foundation (PUL-71): current auth, BQ patterns, FastAPI structure, tests — groundwork for Firebase Auth + JWT cookie + users table"
tags: [research, codebase, auth, bigquery, fastapi, firebase, jwt, pul-71]
status: complete
last_updated: 2026-07-17
last_updated_by: Claude (Fable 5)
---

# Research: Auth foundation (PUL-71)

**Date**: 2026-07-17T14:02:21+02:00
**Git Commit**: b74cda7 · **Branch**: pul-71-auth-foundation · **Repository**: puls-gpw

## Research Question

Jak dziś działa auth (X-API-Key/X-Client-Id), warstwa BigQuery, struktura FastAPI, config i testy — i gdzie dokładnie wpiąć Firebase Auth, JWT HttpOnly cookie, tabelę `users`, endpointy `/api/auth/*`, walidację haseł i rate limiting (ticket PUL-71), tak by istniejący API-key auth działał bez zmian.

## Summary

- **Auth jest w całości per-endpoint `Depends`, nie middleware.** Trzy funkcje w `src/api.py:94-116` (`_get_role`, `_require_admin`, `_get_client_id`) to jedyny seam — rozszerzenie `_get_role` o odczyt JWT cookie i `_get_client_id` o `user_id` z JWT nie wymaga zmiany sygnatur na żadnym z ~30 call site'ów (zweryfikowane ast-grepem: 18× `Depends(_get_role)`, 3× `Depends(_require_admin)`, 12× `Depends(_get_client_id)`).
- **Zero cookies/JWT/rate-limitingu dzisiaj** (grep: 0 trafień w kodzie .py). Brak CORS, brak SessionMiddleware; jedyny middleware to timing (`src/api.py:237-243`). GDPR-notka w UI twierdzi „bez cookies" (`static/index.html:815`) — wymaga aktualizacji.
- **Wzorzec nowej tabeli BQ = triplet**: schema const + `create_*_if_not_exists()` + `ensure_*_schema_current()` delegujące do generycznego `ensure_schema_current()` (`db/bigquery.py:149-181`). Najczystszy szablon: blok watchlist `db/bigquery.py:460-488`. Wartości zawsze przez `ScalarQueryParameter` (104 użycia), nigdy f-string.
- **Dwie nazwy klucza użytkownika w BQ**: `client_id` (watchlist, `db/bigquery.py:463`) i `user_id` (portfolio, `:494,:672`) — obie STRING, obie niosą ten sam anonimowy UUID z `localStorage.watchlist_client_id`. PUL-71 wprowadza trzecie źródło (`Firebase UID`) do tej samej roli — decyzja mapowania należy do planu (PUL-74 zrobi właściwą izolację).
- **Brak routerów** (0× `APIRouter` w repo) — 729-liniowy `create_app()` z closure'ami; `/api/portfolio/*` to precedens prefiksu dla `/api/auth/*`.
- **Zależności do dodania** (`uv add`): `firebase-admin`, biblioteka JWT (np. `pyjwt`), `slowapi` (lub własny licznik), `email-validator` — żadnej z nich nie ma w `pyproject.toml`.
- **Deploy sekretów**: Cloud Run service używa `--set-secrets` z semantyką **replace** (`.github/workflows/deploy.yml:90`) — `JWT_SECRET` i `FIREBASE_SERVICE_ACCOUNT_JSON` trzeba dopisać do istniejącej listy, nie osobno. Tworzenie sekretów w Secret Managerze = krok ręczny (konwencja z `infra.md`).

## Detailed Findings

### 1. Obecny auth — dokładna anatomia

- `src/api.py:94-95` — `APIKeyHeader(name="X-API-Key", auto_error=False)` i `APIKeyHeader(name="X-Client-Id", auto_error=False)`
- `src/api.py:99-104` — `_get_role()`: porównanie plain-string z env `ADMIN_API_KEY` (:100) / `USER_API_KEY` (:102); **401 powstaje tutaj** (:104)
- `src/api.py:107-110` — `_require_admin()` → 403 dla nie-admina; `src/api.py:113-116` — `_get_client_id()` → 400 gdy brak nagłówka
- Startup guard wymaga obu kluczy: `api_main.py:12-14`; `.env.example` ich **nie** listuje
- Publiczne bez auth: `/health` (:256), `/` (:260), `/static/*` (:727). Admin-only: `/admin/x-posts` (:426), `/admin/portfolio/treemap` (:440), `DELETE /announcements/{id}` (:484)
- Rola zmienia też kształt odpowiedzi: `AnnouncementAdmin` vs `AnnouncementUser` (sentiment usunięty) w `/announcements` (`src/api.py:283-307`)

**Frontend** (`static/index.html`):
- Klucz API + rola → **sessionStorage** (:841-842, zapis :1040-1041); client id → **localStorage** `watchlist_client_id`, `crypto.randomUUID()` (:947-956)
- Login: pojedynczy input `#api-key-input` (:704-716) → `fetch('/auth/role')` (:1037); brak pola username
- Każdy fetch dodaje nagłówki inline (brak centralnego wrappera) — ~18 call site'ów, każdy z `if (r.status === 401) doLogout()` (m.in. :996-998, :1571-1573, :2832-2833)
- `doLogout()` (:905-919) czyści sessionStorage; idle-timeout 10 min z ostrzeżeniem od 8 min, client-side only (:855-903)

### 2. Warstwa BigQuery — wzorce dla tabeli `users`

- Generic migrator `ensure_schema_current(table_name, schema)` `db/bigquery.py:149-181` — **additive-only, tylko NULLABLE**; kolumny REQUIRED (np. `created_at`) muszą być w initial create (`:1815-1816`)
- Szablon nowej tabeli: watchlist `db/bigquery.py:460-488` (schema const :462-466, create :469-479, ensure :482-488)
- Kanoniczny MERGE upsert: `upsert_user_portfolio_position()` `:523-574`; plain INSERT: `insert_announcement()` `:1057-1093`; INSERT-if-absent w 1 round-tripie: `add_watchlist_ticker()` `:855-869` i `create_user_portfolio()` `:740-782` (sprawdza `num_dml_affected_rows`)
- Klient: singleton `_get_client()` `:85-105` z guardem `with_quota_project` (:97-103); `_DATASET` czytany **przy imporcie** (:44) → `load_dotenv()` musi być pierwsze w entry poincie (`api_main.py:1-3`)
- Timestamps: „teraz" = SQL `CURRENT_TIMESTAMP()` w treści zapytania (np. `:551,:555`); instanty od callera = `ScalarQueryParameter(..., "TIMESTAMP", dt)` (:1083). Dla `users.last_login_at` → wzorzec UPDATE z `CURRENT_TIMESTAMP()`
- Round-trip: `scripts/test_bq.py` (create+ensure w Step 1 `:49-59`, cleanup w finally `:186-213`); nowa tabela `users` → dopisać create/ensure + blok round-trip + cleanup wzorem watchlist (`:170-185`); istnieją też sibling-skrypty per tabela (`scripts/test_bq_user_portfolios.py` itd.)

### 3. Struktura FastAPI, config, deploy

- App factory `create_app()` `src/api.py:232-729`; endpointy = closure'y `@app.get/post/delete`, **0 routerów** (zweryfikowane); prefiks `/api/*` to nowsza generacja (`/api/portfolio/*` :493-718)
- Pydantic v2 tylko jako modele request/response (`src/api.py:128-229`, `ConfigDict(extra="ignore")`); przykład body: `PortfolioPositionIn` :210-217 użyty w POST :530-535
- 422: automatyczne (Pydantic/Query/Literal) + ręczne `HTTPException(422)` dla walidacji biznesowej (:537, :548, :703-705) — walidacja hasła 8-128+litera+cyfra pasuje do obu wzorców (Pydantic validator lub ręczny raise)
- Błędy: brak custom handlerów; konwencja `try/except BigQueryError → HTTPException(500, str(exc))` (:308-310) — **leakuje surowe błędy BQ**; 404 przez string-matching treści błędu (:488-489)
- Config: czysty `os.environ`, brak pydantic-settings; env-guard w `api_main.py:12-14` — tu dopisać `JWT_SECRET`/`FIREBASE_SERVICE_ACCOUNT_JSON` jeśli obowiązkowe
- Deps (`pyproject.toml:7-31`): brak firebase-admin/pyjwt/slowapi/email-validator; dodawanie przez `uv add` / `uv add --dev` (AGENTS.md:26-27)
- Deploy: `deploy.yml:81-97` — serwis `puls-gpw-api`, `--set-secrets="ADMIN_API_KEY=admin-api-key:latest,USER_API_KEY=user-api-key:latest"` (:90, **replace semantics** — nowe sekrety dopisać do tej listy), `--set-env-vars` (:91); jobs używają addytywnego `--update-secrets` (:60, uzasadnienie `infra.md:26-29`)
- Cloud Run terminuje TLS na swoim proxy; brak `root_path`/proxy configu w `api_main.py:19` — cookie `Secure=True` można ustawiać warunkowo od env (prod) bez konfliktu z czymkolwiek istniejącym

### 4. Testy — gdzie wpiąć nowe

- Unit API: `tests/test_api.py` — `TestClient(create_app())` (:28-30), autouse `_env` ustawia klucze (:12-15); **konwencja: patch na `src.api.<fn>`** (import-site), nie `db.bigquery.<fn>` (:59, :74). TestClient obsługuje cookies → testy register/login/logout/me pasują 1:1
- BQ unit: `tests/test_bigquery.py` — patch `db.bigquery._get_client` MagicMockiem; **mocki nie weryfikują składni SQL** → round-trip na realnym BQ obowiązkowy (lekcja PUL-29, reserved keywords)
- E2E: jedyny conftest `tests/e2e/conftest.py`; `live_server_url` (:338-411) montuje **ExitStack 30 patchy** `src.api.*` (pełna lista :343-392) + uvicorn na wątku; nowe funkcje BQ (`create_users_table_if_not_exists`, `ensure_users_schema_current`, `insert_user`, `get_user_by_email`, `update_last_login`…) **muszą** dołączyć do tej listy + mock weryfikacji Firebase — inaczej E2E uderzy w prawdziwe BQ/Firebase (lekcja z pamięci: conftest-bq-mocking)
- E2E login dzisiaj: lokalny `_login()` per plik wpisuje `e2e-admin-key`/`e2e-user-key` (`test_idle_timeout.py:13-14`, `test_my_wallet.py:6-10`); idle-testy używają `page.clock`

### 5. Rate limiting — stan zerowy

Brak jakiegokolwiek mechanizmu (grep: 0). `slowapi` nie jest zainstalowane. Cloud Run: min 0 / max 2 instancje — in-memory licznik per instancja jest akceptowalny dla progów z ticketa (5/min i 10/min), ale plan musi odnotować, że limit jest per-instancja, nie globalny (max 2 instancje ⇒ efektywnie do 2× limit).

## Code References

- `src/api.py:94-116` — cały seam auth (3 dependency); `:99-104` — `_get_role` + 401
- `src/api.py:232-243` — `create_app()` + jedyny middleware (timing)
- `src/api.py:245-254` — startup hook create/ensure tabel (deprecated `@app.on_event`) — tu dołączyć `users`
- `api_main.py:1-19` — `load_dotenv()` → env-guard (:12-14) → uvicorn :8080
- `db/bigquery.py:460-488` — szablon triplet tabeli (watchlist); `:149-181` — generic `ensure_schema_current`
- `db/bigquery.py:523-574` — kanoniczny MERGE; `:740-782` — insert z guardem unikalności w 1 zapytaniu
- `static/index.html:704-716, 1032-1049` — ekran i handler logowania; `:841-842, 947-956` — storage tożsamości; `:815` — notka GDPR „bez cookies"
- `tests/e2e/conftest.py:338-411` — `live_server_url` + lista 30 mocków `src.api.*`
- `.github/workflows/deploy.yml:81-97` — deploy `puls-gpw-api`; `:90` — `--set-secrets` (replace!)
- `pyproject.toml:7-31` — deps; `:40-47` — E402 exempcje (env przy imporcie)

## Architecture Insights

1. **Dependency-injection zamiast middleware** — ticket mówi „middleware update", ale w praktyce to rozszerzenie `_get_role`/`_get_client_id`; zmiana zlokalizowana, call site'y nietknięte. JWT cookie czytany przez `fastapi Cookie(...)`/`Request.cookies` w tych funkcjach.
2. **Tożsamość dziś = anonimowy UUID przeglądarki**; `auth_type: "api_key"` w JWT payload (wg ticketa) pozwoli middleware'owi mapować starych userów bez migracji danych. Kolizja nazw `client_id`/`user_id` w BQ to znany dług — PUL-71 nie musi go spłacać (izolacja to PUL-74), ale `users.user_id` = Firebase UID ustanawia trzeci format wartości w tych kolumnach.
3. **Firebase Admin SDK nie weryfikuje haseł** — logowanie email+hasło wymaga wywołania Identity Toolkit REST (`signInWithPassword`, klucz `client.apiKey` z konfigu projektu) albo przyjęcia ID tokenu z klienckiego SDK; ticket zakłada „verifies Firebase ID token" — do rozstrzygnięcia w planie, który wariant (REST z backendu jest spójny z obecnym brakiem klienckiego SDK).
4. **Lessons obowiązujące ten change**: (a) `load_dotenv()` przed importami GCP + `with_quota_project` guard przy nowym kliencie (firebase-admin czyta credentials z env — analogiczna zasada); (b) reserved keywords BQ → backticki; mocki nie łapią składni SQL → round-trip `scripts/test_bq.py` obowiązkowy dla tabeli `users`.
5. **In-memory rate limit jest OK na start** (max 2 instancje), ale musi być jawnie nazwany trade-offem w planie.

## Historical Context (from prior changes)

- `context/archive/2026-06-11-auth-public-url/research.md:223-237` — decyzja: Cloud Run `--allow-unauthenticated`, auth w warstwie aplikacji; sekret `admin-api-key` w Secret Managerze przez `--set-secrets`
- `context/archive/2026-06-19-session-inactivity-timeout/plan.md:90-102` — server-side revocation świadomie odroczone „do per-user sessions" — PUL-71 to realizuje
- `context/archive/2026-06-22-my-wallet-watchlist/plan.md:216-226, 342-350` — narodziny `X-Client-Id` + `_get_client_id`; kolumna `client_id`
- `context/archive/2026-06-27-pul-65/research.md:59-61, 117-128` — „UUID jest jedyną tożsamością — no registration, no email, no server-side user record"; kolumna `user_id` niesie tę samą wartość co `client_id`
- `context/archive/2026-06-25-non-admin-portfolio-treemap/research.md:74` — „No JWT — the same X-Client-Id UUID identifies the user across all per-user tables"
- `context/archive/2026-06-19-profile-menu-dropdown/change.md:15` — dropdown profilu zbudowany jawnie jako extensible shell pod przyszłe user menu

## Related Research

- `context/changes/pul-71-auth-foundation/change.md` — zakres + prereq Firebase (zrobiony 2026-07-17)
- `context/foundation/infra.md:23-40` — mapowanie sekretów Secret Manager → serwisy/joby

## Open Questions

1. **Wariant logowania**: backend woła Identity Toolkit REST `signInWithPassword` (spójne z brakiem klienckiego SDK; wymaga `client.apiKey` projektu) vs frontend używa Firebase JS SDK i wysyła ID token (ticket wspomina „verifies Firebase ID token") — decyzja do planu; dotyka też PUL-72.
2. **Skąd `user_id` w JWT dla `auth_type: "api_key"`** — dotychczasowy UUID z `X-Client-Id`? Ticket mówi „no migration", więc najprościej: API-key path w ogóle nie dostaje cookie, działa jak dziś (nagłówki) — potwierdzić w planie.
3. **`users` w startup hooku** (`src/api.py:245-254`) czy tworzone tylko przez register? Konwencja repo: create+ensure na starcie — przyjąć konwencję.
4. **Rate limiter**: `slowapi` (dep + dekoratory) vs własny licznik in-memory (~30 linii, zero deps) — trade-off do planu; oba per-instancja na Cloud Run.
5. **Sekrety prod**: utworzenie `jwt-secret` i `firebase-service-account-json` w Secret Managerze = krok ręczny (human-only konwencja) przed merge — zaplanować jako manual checkpoint.
