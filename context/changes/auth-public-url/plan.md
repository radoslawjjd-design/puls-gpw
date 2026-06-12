# Auth + Public URL — Implementation Plan

## Overview

Wdrożenie FastAPI admin API jako Cloud Run Service z dwoma poziomami dostępu (admin / user),
panelem HTML z filtrowaniem ogłoszeń oraz automatycznym deploymentem przez CI/CD.
Cel: spełnienie wymagania certyfikacyjnego 10xBuilder (widoczny RBAC + publiczny URL).

## Current State Analysis

- FastAPI + uvicorn zainstalowane w `pyproject.toml:8-9`, zero kodu aplikacyjnego
- `delete_announcement(id)` istnieje w `db/bigquery.py:352` ✅
- `fetch_top_n_for_window()` (`db/bigquery.py:266`) — wzorzec SELECT do naśladowania
- Brak `list_announcements_*` — do dodania w Phase 1
- `ADMIN_API_KEY` secret stworzony w Secret Manager (version 1) ✅
- `USER_API_KEY` secret — do stworzenia w Phase 5
- `deploy.yml` obsługuje tylko Jobs (linie 44–57), brak Service step
- `tach.toml` wymaga rejestracji `api_main` jako nowego modułu

### Key Discoveries

- `db/bigquery.py:266-311` — pełny wzorzec: `QueryJobConfig` + parametryzowane query + `try/except → BigQueryError` + `[dict(row) for row in rows]`
- `db/bigquery.py:352-368` — `delete_announcement()` gotowy do reużycia
- `main.py:1-7` — `load_dotenv()` MUSI być pierwszym wywołaniem przed każdym importem `db.*` / `src.*`
- `src/logging_setup.py` — JSON logger, `logger = logging.getLogger(__name__)` w każdym module
- `tests/test_bigquery.py:16-40` — wzorzec mock BQ: `patch("db.bigquery._get_client")`
- `tach.toml:9-14` — `main` i `post_main` zależą od `src` + `db` — `api_main` musi mieć identyczny wpis

## Desired End State

Po wdrożeniu:
1. `GET https://puls-gpw-api-<hash>.run.app/` — panel HTML z formularzem logowania
2. Admin (klucz `ADMIN_API_KEY`): widzi wszystkie ogłoszenia ze wszystkimi polami, może filtrować i usuwać
3. User (klucz `USER_API_KEY`): widzi tylko zatwierdzone ogłoszenia, subset pól, `structured_analysis` jako sparsowany obiekt, brak DELETE
4. CI/CD deployuje Service automatycznie przy każdym push na `master`

### Weryfikacja end state

```bash
# Health — publiczny
curl https://puls-gpw-api-<hash>.run.app/health
# → {"status": "ok"}

# Rola — admin
curl -H "X-API-Key: $ADMIN_API_KEY" https://puls-gpw-api-<hash>.run.app/auth/role
# → {"role": "admin"}

# Ogłoszenia — user, z filtrem
curl -H "X-API-Key: $USER_API_KEY" "https://puls-gpw-api-<hash>.run.app/announcements?ticker=PKO&limit=5"
# → [{company, ticker, event_type, structured_analysis: {...}, analysis_score, published_at}, ...]

# Delete — user (powinien zwrócić 403)
curl -X DELETE -H "X-API-Key: $USER_API_KEY" https://.../announcements/some-id
# → 403
```

## What We're NOT Doing

- Pełne username/password auth — to PUL-23
- OAuth / SSO — osobny ticket
- Swagger UI na produkcji (domyślnie FastAPI udostępnia `/docs`, wystarczy)
- Osobny Dockerfile dla Service — jeden obraz, różne entrypointy
- Paginacja kursorowa — `LIMIT` + `OFFSET` to zadanie PUL-23 przy pełnym UI
- Rate limiting endpointów

## Implementation Approach

Jeden Cloud Run Service obok istniejących Jobs. Wspólny obraz Docker; Service startuje
przez `--command=uv --args="run,python,api_main.py"`. Auth przez dwa klucze API (`ADMIN_API_KEY`,
`USER_API_KEY`) z rolą resolveną po stronie FastAPI. Frontend: jeden plik `static/index.html`
serwowany przez `HTMLResponse` (bez `aiofiles`). BQ zapytania dynamicznie budowane
przez helper `_build_filter_clauses()`.

## Critical Implementation Details

**`load_dotenv()` w `api_main.py`** — musi być absolutnie pierwszą instrukcją przed
jakimkolwiek importem `src.*` lub `db.*`. `BIGQUERY_DATASET` i `GOOGLE_CLOUD_PROJECT`
są czytane przy imporcie modułu `db.bigquery`. Naruszenie tej kolejności powoduje
cichy błąd (brak dataset).

**`from` jako alias query parametru** — `from` jest reserved keyword w Pythonie.
FastAPI wymaga `Query(None, alias="from")` dla parametru `from_dt`. Pominięcie
aliasu powoduje `SyntaxError`.

**`structured_analysis` — `json5.loads()`, nie `json.loads()`** — Gemini zwraca
JSON z trailing commas (~14% przypadków, per `context/foundation/lessons.md`).
`json.loads()` rzuca `JSONDecodeError`. Zawsze `json5.loads()`.

**`static/index.html` — ścieżka względna do WORKDIR** — Dockerfile ustawia
`WORKDIR /app`. Plik `static/index.html` kopiowany z repo będzie w `/app/static/index.html`.
`Path("static/index.html").read_text()` działa poprawnie w tym kontekście.

---

## Phase 1: BQ Data Layer

### Overview

Dodanie dwóch nowych funkcji zapytań z dynamicznym filtrowaniem do `db/bigquery.py`
oraz testów jednostkowych. Reszta systemu nie zmienia się w tej fazie.

### Changes Required

#### 1. Prywatny helper filtrów

**File**: `db/bigquery.py`

**Intent**: Wyodrębnić budowanie klauzuli WHERE i listy parametrów do reużywalnego
helpera prywatnego, by uniknąć duplikacji między `list_announcements_admin`
i `list_announcements_user`.

**Contract**: Dodaj `_build_filter_clauses(approved_only, ticker, company, event_type, from_dt, to_dt) -> tuple[str, list]` zwracający `(where_clause, params_list)`. Pusta klauzula gdy brak filtrów.
Filtr `company` używa `LOWER(company) LIKE LOWER(@company)` z `%{value}%` wildcards.
Filtr `approved_only=True` dodaje `analysis_approved = TRUE` jako pierwszy warunek.

```python
def _build_filter_clauses(
    approved_only: bool = False,
    ticker: str | None = None,
    company: str | None = None,
    event_type: str | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
) -> tuple[str, list[bigquery.ScalarQueryParameter]]:
    clauses, params = [], []
    if approved_only:
        clauses.append("analysis_approved = TRUE")
    if ticker:
        clauses.append("ticker = @ticker")
        params.append(bigquery.ScalarQueryParameter("ticker", "STRING", ticker))
    if company:
        clauses.append("LOWER(company) LIKE LOWER(@company)")
        params.append(bigquery.ScalarQueryParameter("company", "STRING", f"%{company}%"))
    if event_type:
        clauses.append("event_type = @event_type")
        params.append(bigquery.ScalarQueryParameter("event_type", "STRING", event_type))
    if from_dt:
        clauses.append("published_at >= @from_dt")
        params.append(bigquery.ScalarQueryParameter("from_dt", "TIMESTAMP", from_dt))
    if to_dt:
        clauses.append("published_at <= @to_dt")
        params.append(bigquery.ScalarQueryParameter("to_dt", "TIMESTAMP", to_dt))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params
```

#### 2. `list_announcements_admin()`

**File**: `db/bigquery.py`

**Intent**: Zwrócić wszystkie wiersze (bez filtra `analysis_approved`) ze wszystkimi
kolumnami tabeli, z opcjonalnymi filtrami. Używana przez admin role w API.

**Contract**: Sygnatura `list_announcements_admin(limit, ticker, company, event_type, from_dt, to_dt) -> list[dict]`.
Naśladuje wzorzec `fetch_top_n_for_window` (linie 266–311): `_get_client()` → `_build_filter_clauses()` → `QueryJobConfig` z `@limit` jako `INT64` + params z helpera → `try/except → BigQueryError`.
SELECT: wszystkie 17 kolumn ze schematu. `ORDER BY published_at DESC`.

#### 3. `list_announcements_user()`

**File**: `db/bigquery.py`

**Intent**: Zwrócić tylko zatwierdzone ogłoszenia (`analysis_approved = TRUE`)
z podzbiorem 6 kolumn. Używana przez user role w API.
`structured_analysis` zwracana jako raw string (parsowanie w warstwie API).

**Contract**: Sygnatura identyczna jak `list_announcements_admin`.
SELECT: `company, ticker, event_type, structured_analysis, analysis_score, published_at`.
`_build_filter_clauses(approved_only=True, ...)`. `ORDER BY published_at DESC`.

#### 4. Testy BQ

**File**: `tests/test_bigquery.py`

**Intent**: Pokryć nowe funkcje wzorcem mocka istniejącym w pliku (linie 16–40).

**Contract**: Dwa testy dla każdej funkcji:
- bez filtrów (weryfikacja SELECT + ORDER BY)
- z filtrem `ticker` (weryfikacja że parametr ląduje w `job_config`)
Używaj `_mock_bq_client_with_rows([{...}])` i `patch("db.bigquery._get_client")`.

### Success Criteria

#### Automated Verification

- `uv run pytest tests/test_bigquery.py -v` — wszystkie nowe testy zielone
- `uv run tach check` — brak nowych naruszeń granic modułów

#### Manual Verification

- Brak; zmiany czysto wewnętrzne, bez deploymentu

---

## Phase 2: FastAPI Application

### Overview

Stworzenie `src/api.py` z `create_app()` factory i czterema endpointami,
`api_main.py` jako entrypoint uvicorn, aktualizacja `tach.toml`.

### Changes Required

#### 1. `src/api.py` — app factory

**File**: `src/api.py` (nowy plik)

**Intent**: FastAPI app factory z auth dependency, czterema endpointami i dwoma
modelami odpowiedzi. `create_app()` zwraca skonfigurowaną instancję `FastAPI`.

**Contract**:

Modele Pydantic:
- `AnnouncementAdmin` — wszystkie 17 kolumn; `structured_analysis: str | None`
  (raw JSON string dla admina); `published_at: datetime`; `ConfigDict(extra="ignore")`
- `AnnouncementUser` — 6 pól: `company`, `ticker`, `event_type`,
  `structured_analysis: dict | None` (sparsowany), `analysis_score`, `published_at`

Auth:
```python
_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
Role = Literal["admin", "user"]

def _get_role(key: str | None = Security(_API_KEY_HEADER)) -> Role:
    # Porównuje z os.environ["ADMIN_API_KEY"] i os.environ["USER_API_KEY"]
    # Raises HTTPException(401) gdy klucz nie pasuje do żadnego

def _require_admin(role: Role = Depends(_get_role)) -> Role:
    # Raises HTTPException(403) gdy role != "admin"
```

Helper parsowania:
```python
def _parse_structured_analysis(raw: str | None) -> dict | None:
    # json5.loads(raw) z try/except → None przy błędzie parsowania
    # ZAWSZE json5, nigdy json (trailing comma risk)
```

Serwowanie UI:
```python
# _UI czytane wewnątrz create_app() — NIE na poziomie modułu.
# Moduł musi być importowalny bez static/index.html (testy, Phase 2 smoke test).

# W create_app():
#   ui_html = pathlib.Path("static/index.html").read_text(encoding="utf-8")
#   @app.get("/", response_class=HTMLResponse)
#   async def ui() -> str:
#       return ui_html
```

Endpointy:
- `GET /health` — bez auth; `{"status": "ok"}`
- `GET /auth/role` — `Depends(_get_role)`; `{"role": role}`
- `GET /announcements` — `Depends(_get_role)` + query params:
  `limit: int = Query(20, ge=1, le=100)`,
  `ticker`, `company`, `event_type: str | None = Query(None)`,
  `from_dt: datetime | None = Query(None, alias="from")`,
  `to_dt: datetime | None = Query(None, alias="to")`.
  Admin → `list_announcements_admin(...)` → `list[AnnouncementAdmin]`.
  User → `list_announcements_user(...)` → `list[AnnouncementUser]`
  z `structured_analysis=_parse_structured_analysis(row["structured_analysis"])`.
  `BigQueryError` → `HTTPException(500)`.
- `DELETE /announcements/{announcement_id}` — `Depends(_require_admin)`;
  `delete_announcement(id)`; 204 No Content.
  `BigQueryError` z `"no row matched"` w komunikacie → `HTTPException(404)`.
  Inne `BigQueryError` → `HTTPException(500)`.

#### 2. `api_main.py` — uvicorn entrypoint

**File**: `api_main.py` (nowy plik, root projektu)

**Intent**: Entry point dla Cloud Run Service; identyczny wzorzec inicjalizacji
co `main.py` — `load_dotenv()` jako absolutnie pierwsza instrukcja.

**Contract**: `load_dotenv()` → `configure_logging()` → import `src.api` →
`uvicorn.run(create_app(), host="0.0.0.0", port=8080, log_config=None)`.
`log_config=None` zachowuje JSON logger skonfigurowany przez `configure_logging()`.

#### 3. `tach.toml` — rejestracja `api_main`

**File**: `tach.toml`

**Intent**: Zarejestrować `api_main` jako moduł z tymi samymi zależnościami
co `main` i `post_main` — bez tego `tach check` zgłosi naruszenie.

**Contract**: Dodać blok analogiczny do `main` (linie 8–13):
```toml
[[modules]]
path = "api_main"
depends_on = [
    { path = "src" },
    { path = "db" },
]
```

### Success Criteria

#### Automated Verification

- `uv run pytest tests/ -v` — wszystkie testy zielone (faza nie dodaje nowych testów API — to Phase 4)
- `uv run tach check` — brak naruszeń
- `uv run python -c "from src.api import create_app; app = create_app(); print('OK')"` — importuje bez błędów

#### Manual Verification

- `uv run python api_main.py` (lokalnie z `.env`) → serwer startuje na `0.0.0.0:8080`
- `curl localhost:8080/health` → `{"status": "ok"}`
- `curl -H "X-API-Key: $ADMIN_API_KEY" localhost:8080/auth/role` → `{"role": "admin"}`
- `curl -H "X-API-Key: $USER_API_KEY" localhost:8080/auth/role` → `{"role": "user"}`
- `curl -H "X-API-Key: wrongkey" localhost:8080/auth/role` → 401

---

## Phase 3: Frontend (static/index.html)

### Overview

Jeden plik HTML z inline CSS i JavaScript. Dwa widoki: ekran logowania i dashboard.
Nie wymaga żadnych nowych zależności Python.

### Changes Required

#### 1. `static/index.html`

**File**: `static/index.html` (nowy plik + nowy katalog `static/`)

**Intent**: Kompletny panel admina/usera w jednym pliku HTML. Obsługuje
logowanie przez API key, detekcję roli, filtrowanie i przeglądanie danych.
Admin widzi przycisk [Usuń] i pełne dane. User widzi podzbiór pól bez DELETE.

**Contract** — przepływ JS:

```
Startup:
  key = sessionStorage.getItem("apiKey")
  role = sessionStorage.getItem("role")
  if key && role → showDashboard(role)
  else → showLogin()

Login submit:
  key = input.value
  GET /auth/role z X-API-Key: key
  jeśli 200 → sessionStorage.setItem("apiKey", key) + .setItem("role", role)
            → showDashboard(role)
  jeśli 401 → error "Nieprawidłowy klucz API"

Fetch announcements (na load dashboardu i submit filtrów):
  params = zbierz z formularza (limit, ticker, company, event_type, from, to)
  GET /announcements?{params} z X-API-Key
  renderTable(data, role)

renderTable(data, role):
  Admin: kolumny — Data, Spółka, Ticker, Typ, Score, URL, [Usuń]
  User: kolumny — Data, Spółka, Ticker, Typ, Score, Analiza (structured_analysis.summary_pl)

Delete (admin only):
  DELETE /announcements/{id} z X-API-Key
  jeśli 204 → usuń wiersz z tabeli
  jeśli 404 → alert "Nie znaleziono"

Logout:
  sessionStorage.clear() → showLogin()
```

**UI layout**:
- Login: wycentrowany formularz, pole `type="password"`, przycisk "Zaloguj się"
- Dashboard: nagłówek z [Wyloguj], formularz filtrów (ticker, spółka, typ, od, do, limit),
  tabela responsywna, odświeżanie na submit filtrów

Styl: minimalistyczny, bez zewnętrznych zależności CSS (inline `<style>`).

### Success Criteria

#### Automated Verification

- `uv run python api_main.py` startuje bez błędów (plik `static/index.html` istnieje)
- `curl localhost:8080/` → HTTP 200, Content-Type: `text/html`

#### Manual Verification

- Otwórz `localhost:8080/` w przeglądarce → widać formularz logowania
- Wpisz `ADMIN_API_KEY` → wchodzisz na dashboard, widoczny przycisk [Usuń], wszystkie kolumny
- Wpisz `USER_API_KEY` → wchodzisz na dashboard, brak [Usuń], `structured_analysis.summary_pl` zamiast raw JSON
- Wpisz błędny klucz → komunikat błędu, brak przekierowania
- Filtruj po tickerze → tabela odświeża się z wynikami
- Kliknij [Wyloguj] → wraca ekran logowania, sessionStorage wyczyszczone
- Odśwież stronę będąc zalogowanym → zostaje zalogowany (sessionStorage persists)

---

## Phase 4: Tests

### Overview

Kompletne testy jednostkowe dla `src/api.py` przez `TestClient`. Pokrycie:
auth (obie role, brak klucza), endpointy, role boundaries, parsowanie `structured_analysis`.

### Changes Required

#### 1. `tests/test_api.py`

**File**: `tests/test_api.py` (nowy plik)

**Intent**: Testy wszystkich endpointów API z obydwoma rolami i kluczami krawędziowymi.
Naśladują wzorzec `tests/test_bigquery.py` — brak `conftest.py`, mockowanie inline.

**Contract** — przypadki testowe:

```
GET /health
  - test_health_no_auth_returns_200

GET /auth/role
  - test_auth_role_admin_key_returns_admin
  - test_auth_role_user_key_returns_user
  - test_auth_role_invalid_key_returns_401
  - test_auth_role_missing_key_returns_401

GET /announcements
  - test_announcements_admin_returns_all_fields        (mock list_announcements_admin)
  - test_announcements_user_returns_subset_fields      (mock list_announcements_user)
  - test_announcements_user_parses_structured_analysis (structured_analysis jako dict)
  - test_announcements_no_key_returns_401
  - test_announcements_bq_error_returns_500
  - test_announcements_filter_ticker_passed_to_bq      (weryfikacja że query param trafia do BQ fn)

DELETE /announcements/{id}
  - test_delete_admin_returns_204
  - test_delete_user_returns_403
  - test_delete_no_key_returns_401
  - test_delete_not_found_returns_404                  (BigQueryError z "no row matched")
  - test_delete_bq_error_returns_500
```

Wzorzec env mock:
```python
_ADMIN_KEY = "test-admin-key"
_USER_KEY = "test-user-key"

@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", _ADMIN_KEY)
    monkeypatch.setenv("USER_API_KEY", _USER_KEY)
```

Wzorzec TestClient (naśladuje projekt — `client` jako moduł-level, app tworzona raz):
```python
from fastapi.testclient import TestClient
from src.api import create_app
client = TestClient(create_app())
```

### Success Criteria

#### Automated Verification

- `uv run pytest tests/test_api.py -v` — wszystkie testy zielone
- `uv run pytest --tb=short` — cały suite zielony (brak regresji)

#### Manual Verification

- Brak; testy pokrywają wszystkie ścieżki

---

## Phase 5: CI/CD & Secrets

### Overview

Stworzenie sekretu `user-api-key` w Secret Manager, dodanie deploy step dla
Cloud Run Service w `deploy.yml`.

### Changes Required

#### 0. Weryfikacja uprawnień SA (krok manualny — prerequisite)

**File**: brak (GCP IAM)

**Intent**: Upewnić się, że `puls-gpw-runner` SA ma `roles/run.admin` zanim
zostanie wyzwolony deploy. Bez tej roli `gcloud run deploy` zwróci 403.

**Contract**: Uruchom lokalnie przed pierwszym push:
```bash
gcloud projects get-iam-policy puls-gpw \
  --flatten="bindings[].members" \
  --filter="bindings.role=roles/run.admin AND bindings.members:puls-gpw-runner" \
  --format="value(bindings.members)"
```
Jeśli output jest pusty — nadaj rolę (operacja destruktywna infra, human-only):
```bash
gcloud projects add-iam-policy-binding puls-gpw \
  --member="serviceAccount:puls-gpw-runner@puls-gpw.iam.gserviceaccount.com" \
  --role="roles/run.admin"
```

#### 1. Secret `user-api-key` w Secret Manager (krok manualny)

**File**: brak (GCP Secret Manager)

**Intent**: Stworzyć sekret `user-api-key` z wygenerowaną wartością przed pierwszym
deploymentem, by CI/CD mógł go zbindować do Service przez `--set-secrets`.

**Contract**: Wygeneruj nową wartość USER_API_KEY przed wykonaniem kroku:
`openssl rand -base64 32` (lub `python -c "import secrets; print(secrets.token_urlsafe(32))"`)

```bash
echo -n "<generate-with: openssl rand -base64 32>" | gcloud secrets create user-api-key \
  --data-file=- --replication-policy=automatic --project=puls-gpw
```

#### 2. Deploy step dla Service

**File**: `.github/workflows/deploy.yml`

**Intent**: Dodać krok deployujący Cloud Run Service `puls-gpw-api` po istniejących
dwóch krokach Job. `gcloud run deploy` jest upsert — tworzy Service przy pierwszym
uruchomieniu, aktualizuje obraz przy kolejnych.

**Contract**: Dodać po linii 57 (po "Update Cloud Run Job (post)"):

```yaml
      - name: Deploy Cloud Run Service (api)
        run: |
          gcloud run deploy puls-gpw-api \
            --image="${{ env.IMAGE }}:${{ github.sha }}" \
            --command=uv --args="run,python,api_main.py" \
            --port=8080 \
            --region=${{ env.REGION }} \
            --project=${{ env.PROJECT_ID }} \
            --service-account=puls-gpw-runner@puls-gpw.iam.gserviceaccount.com \
            --set-secrets="ADMIN_API_KEY=admin-api-key:latest,USER_API_KEY=user-api-key:latest" \
            --set-env-vars="GOOGLE_CLOUD_PROJECT=${{ env.PROJECT_ID }},BIGQUERY_DATASET=espi_ebi" \
            --allow-unauthenticated \
            --cpu=1 \
            --memory=512Mi \
            --min-instances=0 \
            --max-instances=2 \
            --timeout=60
```

### Success Criteria

#### Automated Verification

- Push na `master` → GitHub Actions przechodzi: testy ✅, build ✅, Jobs update ✅, Service deploy ✅
- `gcloud run services describe puls-gpw-api --region=europe-central2 --project=puls-gpw` — status: `ACTIVE`

#### Manual Verification

- Skopiuj URL z outputu CI lub: `gcloud run services describe puls-gpw-api --format="value(status.url)"`
- `curl <SERVICE_URL>/health` → `{"status": "ok"}`
- Otwórz `<SERVICE_URL>/` w przeglądarce → panel logowania
- Zaloguj się kluczem admin → dashboard z danymi z produkcyjnego BQ
- Zaloguj się kluczem user → ograniczony widok, brak DELETE
- Wklej URL w formularzu certyfikacyjnym 10xBuilder ✅

---

## Testing Strategy

### Unit Tests

- `tests/test_bigquery.py` — `list_announcements_admin`, `list_announcements_user` z i bez filtrów
- `tests/test_api.py` — wszystkie endpointy, obie role, przypadki błędów

### Manual Testing Steps

1. Uruchom lokalnie: `uv run python api_main.py` (wymaga `.env` z `ADMIN_API_KEY`, `USER_API_KEY`, `GOOGLE_CLOUD_PROJECT`, `BIGQUERY_DATASET`)
2. Zaloguj się na `localhost:8080` obu kluczami, sprawdź role boundaries
3. Sprawdź filtry (ticker, company, event_type, zakres dat)
4. Zweryfikuj DELETE: admin usuwa → 204; user próbuje → 403
5. Po pushu na master sprawdź Service URL z CI output

## References

- Frame: `context/changes/auth-public-url/frame.md`
- Research: `context/changes/auth-public-url/research.md`
- BQ wzorzec SELECT: `db/bigquery.py:266-311`
- BQ delete: `db/bigquery.py:352-368`
- Deploy pattern: `.github/workflows/deploy.yml:44-57`
- Test mock BQ: `tests/test_bigquery.py:16-40`
- Lessons: `context/foundation/lessons.md` (load_dotenv, json5, quota_project)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands.

### Phase 1: BQ Data Layer

#### Automated

- [x] 1.1 `uv run pytest tests/test_bigquery.py -v` — nowe testy zielone
- [x] 1.2 `uv run tach check` — brak naruszeń

### Phase 2: FastAPI Application

#### Automated

- [ ] 2.1 `uv run pytest tests/ -v` — suite zielony (brak regresji)
- [ ] 2.2 `uv run tach check` — brak naruszeń po dodaniu api_main
- [ ] 2.3 Import smoke test: `uv run python -c "from src.api import create_app; create_app()"`

#### Manual

- [ ] 2.4 `curl localhost:8080/health` → `{"status": "ok"}`
- [ ] 2.5 Auth role check — admin key, user key, zły klucz

### Phase 3: Frontend

#### Automated

- [ ] 3.1 `uv run python api_main.py` startuje bez błędów (static/index.html istnieje)
- [ ] 3.2 `curl localhost:8080/` → HTTP 200, `text/html`

#### Manual

- [ ] 3.3 Otwórz `localhost:8080/` w przeglądarce → widać formularz logowania
- [ ] 3.4 Login flow — admin key → dashboard z [Usuń]
- [ ] 3.5 Login flow — user key → dashboard bez [Usuń], summary_pl w tabeli
- [ ] 3.6 Błędny klucz → komunikat błędu
- [ ] 3.7 Filtry działają (ticker, company, typ, daty)
- [ ] 3.8 Wylogowanie → ekran logowania, sessionStorage wyczyszczone
- [ ] 3.9 Odświeżenie strony → sesja zachowana

### Phase 4: Tests

#### Automated

- [ ] 4.1 `uv run pytest tests/test_api.py -v` — wszystkie testy zielone
- [ ] 4.2 `uv run pytest --tb=short` — cały suite zielony

### Phase 5: CI/CD & Secrets

#### Automated

- [ ] 5.1 Push na master → GitHub Actions zielony (testy + build + 3 deploy steps)
- [ ] 5.2 `gcloud run services describe puls-gpw-api` → status ACTIVE

#### Manual

- [ ] 5.3 `curl <SERVICE_URL>/health` → `{"status": "ok"}`
- [ ] 5.4 Panel HTML dostępny pod publicznym URL
- [ ] 5.5 Login admin + user na produkcji — obie role działają
