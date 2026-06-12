# Auth + Public URL — Plan Brief

> Full plan: `context/changes/auth-public-url/plan.md`
> Frame brief: `context/changes/auth-public-url/frame.md`
> Research: `context/changes/auth-public-url/research.md`

## What & Why

Wdrożenie FastAPI admin API jako Cloud Run Service z dwupoziomowym RBAC (admin / user)
i panelem HTML z logowaniem i filtrowaniem ogłoszeń. Cel: spełnienie wymagania
certyfikacyjnego 10xBuilder — widoczny mechanizm kontroli dostępu i publiczny URL.

## Starting Point

Projekt działa jako dwa Cloud Run Jobs (scraper + post generator). FastAPI i uvicorn
są zainstalowane w `pyproject.toml`, ale nie ma żadnego kodu HTTP. `delete_announcement()`
istnieje w `db/bigquery.py:352`; brakuje `list_announcements_*`. Secret `admin-api-key`
stworzony w Secret Manager ✅.

## Desired End State

Cloud Run Service `puls-gpw-api` z publicznym URL serwuje panel HTML. Admin (klucz
`ADMIN_API_KEY`) widzi wszystkie ogłoszenia z możliwością usuwania. User (klucz
`USER_API_KEY`) widzi tylko zatwierdzone ogłoszenia z podzbiorem pól i sparsowaną
analizą. CI/CD deployuje Service automatycznie przy każdym push na `master`.

## Key Decisions Made

| Decyzja | Wybór | Dlaczego | Źródło |
|---------|-------|----------|--------|
| Auth mechanizm | Dwa klucze API (admin + user) | Minimalny RBAC wystarczający dla certyfikacji; PUL-23 doda pełne konta | Frame |
| Deployment | `gcloud run deploy` (upsert) w CI | Idempotentne; tworzy Service przy pierwszym push | Plan |
| Obraz Docker | Jeden obraz, różne CMD | Brak duplikacji; Jobs używają `main.py`, Service `api_main.py` | Frame/Research |
| GET /announcements (admin) | Wszystkie wiersze, wszystkie kolumny | Admin widzi pełny pipeline | Plan |
| GET /announcements (user) | Tylko `analysis_approved=TRUE`, 6 pól | User widzi gotowe dane; `structured_analysis` sparsowany | Plan |
| Frontend | Single `static/index.html` z inline JS | Zero nowych zależności; `HTMLResponse` bez `aiofiles` | Plan |
| `structured_analysis` | `json5.loads()` zawsze | Gemini zwraca trailing commas ~14% przypadków (lessons.md) | Research |

## Scope

**In scope:**
- `db/bigquery.py` — `list_announcements_admin()`, `list_announcements_user()` z dynamicznymi filtrami
- `src/api.py` — `create_app()`, 4 endpointy, RBAC dependency, Pydantic models
- `api_main.py` — uvicorn entrypoint
- `static/index.html` — panel HTML z logowaniem, filtrami, tabelą
- `tests/test_bigquery.py` — testy nowych funkcji BQ
- `tests/test_api.py` — testy wszystkich endpointów z obydwoma rolami
- `.github/workflows/deploy.yml` — nowy step Service
- `tach.toml` — rejestracja `api_main`
- Secret Manager — `user-api-key` (manualny krok w Phase 5)

**Out of scope:**
- Pełne username/password auth → PUL-23
- OAuth / SSO
- Rate limiting
- Paginacja kursorowa
- Swagger UI customizacja

## Architecture / Approach

```
static/index.html (przeglądarka)
    │  X-API-Key header
    ▼
Cloud Run Service: puls-gpw-api (api_main.py → uvicorn → src/api.py)
    │  role check (ADMIN_API_KEY | USER_API_KEY z Secret Manager)
    │  filtry: ticker, company, event_type, from, to, limit
    ▼
BigQuery: announcements (db/bigquery.py)
    └─ list_announcements_admin() → wszystkie wiersze
    └─ list_announcements_user()  → tylko approved, subset
    └─ delete_announcement()      → admin only
```

Jeden obraz Docker; `deploy.yml` używa `--command=uv --args="run,python,api_main.py"`
jako override dla Service, Jobs mają własne CMD.

## Phases at a Glance

| Faza | Co dostarcza | Kluczowe ryzyko |
|------|-------------|-----------------|
| 1. BQ Data Layer | `list_announcements_admin/user` z filtrami | Dynamiczne BQ query z opcjonalnymi parametrami — błąd parametryzacji |
| 2. FastAPI App | API z RBAC, entrypoint | `load_dotenv()` ordering, `from` alias w Query |
| 3. Frontend | Panel HTML z logowaniem i filtrowaniem | `sessionStorage` cross-tab behavior; responsywność tabeli |
| 4. Tests | Pełne pokrycie `test_api.py` | Mock env vars dla obu kluczy; TestClient z `create_app()` |
| 5. CI/CD & Secrets | Service na produkcji, publiczny URL | `roles/run.admin` na SA; cold start przy pierwszym curl |

**Prerequisites:**
- Secret `admin-api-key` ✅ (stworzony 2026-06-12)
- Secret `user-api-key` — tworzy Phase 5 (wygeneruj: `openssl rand -base64 32`)
- `puls-gpw-runner` SA musi mieć `roles/run.admin` dla `gcloud run deploy` w CI

**Estimated effort:** ~2 sesje implementacji (5 faz, sekwencyjne)

## Open Risks & Assumptions

- `puls-gpw-runner` SA ma `roles/run.admin` — nie zweryfikowane; jeśli nie, pierwszy CI deploy Service zawiedzie z permission error
- `static/index.html` path (`"static/index.html"`) zakłada `WORKDIR /app` z Dockerfile — nienaruszone
- `structured_analysis` JSON może mieć nieznane pola — `extra="allow"` w Pydantic lub `dict` type

## Success Criteria (Summary)

- `curl <SERVICE_URL>/health` → `{"status": "ok"}` z publicznego internetu
- Login admin i user z różnymi widokami danych — działający w przeglądarce
- `uv run pytest --tb=short` — cały suite zielony przed każdym deployem
