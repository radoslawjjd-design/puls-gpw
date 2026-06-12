---
date: 2026-06-12T00:00:00+02:00
researcher: Claude Sonnet 4.6
git_commit: 1e15e51ca7f6180ffaf7d2e1a8b51fdb2a91f350
branch: master
repository: puls-gpw
topic: "FastAPI Admin API + Cloud Run Service dla certyfikacji 10xBuilder (PUL-17)"
tags: [research, fastapi, cloud-run-service, auth, bigquery, deployment, x-api-key]
status: complete
last_updated: 2026-06-12
last_updated_by: Claude Sonnet 4.6
---

# Research: FastAPI Admin API + Cloud Run Service (PUL-17)

**Date**: 2026-06-12T00:00:00+02:00
**Researcher**: Claude Sonnet 4.6
**Git Commit**: `1e15e51ca7f6180ffaf7d2e1a8b51fdb2a91f350`
**Branch**: master
**Repository**: puls-gpw

## Research Question

Co istnieje w projekcie, co trzeba dodać i jakie są krytyczne wzorce/ograniczenia dla
implementacji FastAPI admin API (GET /health, GET /announcements, DELETE /announcements/{id})
deployowanego jako Cloud Run Service z X-API-Key auth i publicznym URL?

## Summary

1. **BQ** — `delete_announcement()` już istnieje (`db/bigquery.py:352`). Brakuje `list_announcements(limit)` — trzeba dodać wzorując się na `fetch_top_n_for_window()`.
2. **FastAPI** — zainstalowany (`pyproject.toml:8-9`), ale zero kodu aplikacyjnego. Trzeba stworzyć `src/api.py` i `api_main.py`.
3. **Deployment** — CI/CD `deploy.yml` obsługuje tylko Jobs i nie ustawia sekretów przez CI. Nowy Service wymaga nowego stepu `gcloud run deploy` + manualnego stworzenia sekretu `admin-api-key` w Secret Manager.
4. **Auth** — X-API-Key jako FastAPI `Depends()` dependency; Cloud Run Service z `--allow-unauthenticated` (auth w warstwie aplikacji, nie GCP IAM).
5. **Testy** — wzorzec `TestClient(app)` + `patch("db.bigquery._get_client")` + `patch.dict(os.environ)`.

## Detailed Findings

### 1. BigQuery Data Layer (`db/bigquery.py`)

#### Schemat tabeli `announcements` (linie 32–50)

| Kolumna | Typ | Mode | Opis |
|---------|-----|------|------|
| `announcement_id` | STRING | REQUIRED | SHA256 hash URL — klucz dedup |
| `url` | STRING | REQUIRED | URL ogłoszenia |
| `published_at` | TIMESTAMP | REQUIRED | Data publikacji |
| `title` | STRING | REQUIRED | Tytuł |
| `company` | STRING | NULLABLE | Ustawiana przez parser |
| `ticker` | STRING | NULLABLE | Ustawiana przez parser |
| `priority` | STRING | NULLABLE | Badge HTML ze scrapera |
| `parsed_content` | STRING | NULLABLE | Tekst PDF/HTML |
| `structured_analysis` | STRING | NULLABLE | JSON z Gemini |
| `analysis_approved` | BOOL | NULLABLE | Flaga zatwierdzenia |
| `analysis_reject_reason` | STRING | NULLABLE | Powód odrzucenia |
| `event_type` | STRING | NULLABLE | Typ zdarzenia |
| `analysis_score` | FLOAT64 | NULLABLE | Score 0.0–1.0 |
| `post_text` | STRING | NULLABLE | Wygenerowany post X |
| `posted_at` | TIMESTAMP | NULLABLE | Timestamp generacji |
| `analyzed_at` | TIMESTAMP | NULLABLE | Timestamp analizy |
| `supervisor_attempts` | INTEGER | NULLABLE | Liczba prób (1–3) |

#### Istniejące funkcje publiczne BQ

| Funkcja | Linia | Sygnatura | Status |
|---------|-------|-----------|--------|
| `delete_announcement` | 352 | `(announcement_id: str) -> None` | ✅ ISTNIEJE |
| `fetch_top_n_for_window` | 266 | `(start, end, n) -> list[dict]` | wzorzec do naśladowania |
| `insert_announcement` | 143 | `(url, published_at, title, priority) -> str` | — |
| `list_announcements` | — | `(limit: int) -> list[dict]` | ❌ BRAKUJE — dodać |

#### Wzorzec `delete_announcement` (`db/bigquery.py:352-368`)

```python
def delete_announcement(announcement_id: str) -> None:
    client = _get_client()
    query = f"DELETE FROM `{_table_ref(client)}` WHERE announcement_id = @id"
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", announcement_id)]
    )
    try:
        job = client.query(query, job_config=job_config)
        job.result()
    except Exception as exc:
        raise BigQueryError(f"delete_announcement failed: {exc}") from exc
    if job.num_dml_affected_rows == 0:
        raise BigQueryError(f"delete_announcement: no row matched {announcement_id!r}")
```

#### Wzorzec do stworzenia `list_announcements`

Naśladuje `fetch_top_n_for_window` (`db/bigquery.py:266-311`):
- `_get_client()` → singleton z `_client_lock`
- `QueryJobConfig(query_parameters=[ScalarQueryParameter("limit", "INT64", limit)])`
- `try: rows = list(client.query(...).result()) except Exception as exc: raise BigQueryError(...) from exc`
- Return: `[dict(row) for row in rows]`

**Kluczowe**: `load_dotenv()` MUSI być wywołane w entry pointach (`api_main.py`) **przed** importem `db.bigquery` — moduł czyta `GOOGLE_CLOUD_PROJECT` i `BIGQUERY_DATASET` przy imporcie (linie 27-30).

#### Inicjalizacja klienta BQ (`db/bigquery.py:52-76`)

Singleton thread-safe z ADC + quota_project guard:
```python
_client: bigquery.Client | None = None
_client_lock = threading.Lock()

def _get_client() -> bigquery.Client:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                credentials, _ = google.auth.default()
                if hasattr(credentials, "with_quota_project"):  # WIF guard (lessons.md)
                    credentials = credentials.with_quota_project(project)
                _client = bigquery.Client(project=project, credentials=credentials)
    return _client
```

#### Obsługa błędów BQ

- Klasa: `BigQueryError(PipelineStageError)` z `src/exceptions.py:12-13`
- Wzorzec DML: `job.result()` + sprawdzenie `job.num_dml_affected_rows == 0` → `raise BigQueryError`
- Wzorzec SELECT: `try: list(job.result()) except Exception as exc: raise BigQueryError(...) from exc`

---

### 2. Konwencje kodu (`src/`)

#### Krytyczny porządek importów w entry pointach (`main.py:1-14`)

```python
# 1. NAJPIERW load_dotenv — przed KAŻDYM importem src/ i db/
from dotenv import load_dotenv
load_dotenv()

# 2. Logowanie
from src.logging_setup import configure_logging
configure_logging()
logger = logging.getLogger(__name__)

# 3. DOPIERO TERAZ importy modułów czytających env vars
from db.bigquery import ...
from src.analyzer import ...
```

`api_main.py` musi naśladować ten wzorzec identycznie.

#### Konfiguracja logowania (`src/logging_setup.py`)

- JSON formatter, Cloud Logging compatible
- Pola: `levelname → severity`, `asctime → timestamp`
- Output: `sys.stderr`
- Każdy moduł: `logger = logging.getLogger(__name__)`

#### Pydantic vs dataclass

- `BaseModel` (Pydantic) — dane z zewnętrznych źródeł (AI responses, API responses)
- `@dataclass` — struktury wynikowe wewnętrznych funkcji
- `ConfigDict(extra="ignore")` — standardowy config dla modeli przyjmujących zewnętrzne dane
- Dla response models FastAPI: `BaseModel` z `model_config = ConfigDict(extra="ignore")`

#### Obsługa błędów

Hierarchia: `PipelineStageError` → `BigQueryError`, `ScraperError`, `ParserError`, etc.
W API: `BigQueryError` → `HTTPException(status_code=500)` (nie propagować raw BQ errors do klientów).

#### Struktura modułu

```
docstring
imports → logger → constants (os.environ.get()) → models → public functions → private functions
```

Constants na poziomie modułu (nie lazy-binding). `src/__init__.py` pusty — importy bezpośrednie z submodułów.

---

### 3. Deployment i CI/CD (`.github/workflows/deploy.yml`)

#### Obecna struktura deploy.yml (linie 1–57)

| Step | Linia | Co robi |
|------|-------|---------|
| checkout | 18 | — |
| GCP auth | 20–23 | SA JSON z `secrets.puls_gpw_secret` |
| setup-gcloud | 25 | — |
| setup-uv | 27–30 | Python 3.13 |
| **Run tests** | 32–33 | `uv run pytest --tb=short` — blokuje przy błędach |
| Docker auth | 35–36 | Artifact Registry |
| **Build & push** | 38–42 | `docker build -t IMAGE:SHA . && docker push` |
| **Update Job scraper** | 44–50 | `gcloud run jobs update puls-gpw --command=uv --args="run,python,main.py"` |
| **Update Job post** | 52–57 | `gcloud run jobs update puls-gpw-post` |

#### Kluczowe obserwacje

1. **Brak `--set-secrets` w CI** — sekrety dla Jobs są ustawione raz manualnie, CI tylko podmienia obraz. Service powinien naśladować ten wzorzec LUB explicite dodać `--set-secrets` w kroku deploy.
2. **Tag wyłącznie SHA** — `IMAGE:${{ github.sha }}`, bez `:latest`. `infra.md` wspomina `:latest` dla post-joba — to niespójność historyczna.
3. **`gcloud run deploy` = upsert** — tworzy Service jeśli nie istnieje, aktualizuje jeśli istnieje. Właściwy pattern dla CI/CD.

#### Nowy step dla Service (do dodania po linii 57)

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
            --set-secrets="ADMIN_API_KEY=admin-api-key:latest" \
            --set-env-vars="GOOGLE_CLOUD_PROJECT=${{ env.PROJECT_ID }},BIGQUERY_DATASET=espi_ebi" \
            --allow-unauthenticated \
            --cpu=1 \
            --memory=512Mi \
            --min-instances=0 \
            --max-instances=2 \
            --timeout=60
```

**Uwaga**: `--set-secrets` w Service deploy jest explicite (inaczej niż Jobs) — jest to celowa decyzja. Idempotentne i audytowalne.

#### Dlaczego `--allow-unauthenticated`?

Cloud Run IAM (`roles/run.invoker`) używa Google Identity tokens — nie jest przeznaczony dla zewnętrznych klientów API. Gdy auth robiony przez X-API-Key w FastAPI, Service musi być osiągalny bez Google token → `--allow-unauthenticated`. Bezpieczeństwo zapewnia warstwa aplikacji (FastAPI dependency).

#### Manualny krok (jednorazowy, przed pierwszym deploy)

```bash
# Stworzenie sekretu ADMIN_API_KEY w Secret Manager
echo -n "WYGENEROWANY_KLUCZ" | gcloud secrets create admin-api-key \
  --data-file=- \
  --replication-policy=automatic \
  --project=puls-gpw
```

✅ Sekret `admin-api-key` stworzony w Secret Manager (version 1, 2026-06-12). Wartość znana użytkownikowi z sesji tworzenia.

---

### 4. Wzorce testów (`tests/`)

#### Organizacja

- Katalog: `tests/` — płaska struktura, brak `conftest.py`
- Brak shared fixtures — mockowanie inline per test
- Runner: `uv run pytest --tb=short`
- Wszystkie zewnętrzne zależności mockowane przez `unittest.mock.patch`

#### Wzorzec mock BQ (`tests/test_bigquery.py:16-40`)

```python
from unittest.mock import MagicMock, patch

def _mock_bq_client(affected_rows: int = 1) -> MagicMock:
    client = MagicMock()
    client.project = "test-project"
    job = MagicMock()
    job.result.return_value = None
    job.errors = None
    job.num_dml_affected_rows = affected_rows
    client.query.return_value = job
    return client

def _mock_bq_client_with_rows(rows: list[dict]) -> MagicMock:
    client = MagicMock()
    client.project = "test-project"
    mock_rows = [MagicMock(**{k: v for k, v in row.items()}) for row in rows]
    job = MagicMock()
    job.result.return_value = mock_rows
    job.errors = None
    client.query.return_value = job
    return client

# Użycie:
with patch("db.bigquery._get_client", return_value=_mock_bq_client()):
    delete_announcement("some-id")

with patch("db.bigquery._get_client", return_value=_mock_bq_client_with_rows([{...}])):
    result = list_announcements(limit=5)
```

#### Wzorzec TestClient + auth (`tests/test_api.py` — nowy plik)

```python
import os
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from src.api import create_app

app = create_app()
client = TestClient(app)

_VALID_KEY = "test-api-key-123"

def test_health_no_auth_required():
    response = client.get("/health")
    assert response.status_code == 200

def test_announcements_no_key_returns_401():
    response = client.get("/announcements")
    assert response.status_code == 401

def test_announcements_wrong_key_returns_401():
    with patch.dict(os.environ, {"ADMIN_API_KEY": _VALID_KEY}):
        response = client.get("/announcements", headers={"X-API-Key": "wrong"})
    assert response.status_code == 401

def test_announcements_valid_key_returns_200():
    mock_rows = [{"announcement_id": "id1", "title": "Test", ...}]
    with patch.dict(os.environ, {"ADMIN_API_KEY": _VALID_KEY}), \
         patch("db.bigquery._get_client", return_value=_mock_bq_client_with_rows(mock_rows)):
        response = client.get("/announcements?limit=5",
                              headers={"X-API-Key": _VALID_KEY})
    assert response.status_code == 200
```

---

### 5. Architektura nowych plików

#### `api_main.py` (root) — uvicorn entrypoint

```python
"""Uvicorn entrypoint for puls-gpw admin API."""
import logging
from dotenv import load_dotenv

load_dotenv()  # MUSI być pierwszy

from src.logging_setup import configure_logging
configure_logging()
logger = logging.getLogger(__name__)

from src.api import create_app
import uvicorn

if __name__ == "__main__":
    logger.info("Starting puls-gpw API server")
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8080, log_config=None)
```

#### `src/api.py` — FastAPI app factory

```python
"""Admin API for puls-gpw — access control layer."""
import logging
import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict

from db.bigquery import BigQueryError, delete_announcement, list_announcements
from src.exceptions import PipelineStageError

logger = logging.getLogger(__name__)

_MAX_LIMIT = 100
_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

def _verify_api_key(key: str | None = Depends(_API_KEY_HEADER)) -> str:
    admin_key = os.environ.get("ADMIN_API_KEY", "")
    if not admin_key or key != admin_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return key

class AnnouncementItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    announcement_id: str
    url: str
    published_at: str           # ISO string z BQ
    title: str
    company: str | None
    ticker: str | None
    analysis_approved: bool | None
    analysis_score: float | None
    event_type: str | None
    priority: str | None

def create_app() -> FastAPI:
    app = FastAPI(title="puls-gpw admin API", version="1.0.0")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/announcements", dependencies=[Depends(_verify_api_key)])
    async def get_announcements(limit: int = 20) -> list[AnnouncementItem]:
        if limit > _MAX_LIMIT:
            raise HTTPException(status_code=422, detail=f"limit max {_MAX_LIMIT}")
        try:
            rows = list_announcements(limit=limit)
        except BigQueryError:
            logger.exception("BQ list_announcements failed")
            raise HTTPException(status_code=500, detail="Database error")
        return [AnnouncementItem(**row) for row in rows]

    @app.delete("/announcements/{announcement_id}",
                status_code=204, dependencies=[Depends(_verify_api_key)])
    async def remove_announcement(announcement_id: str) -> None:
        try:
            delete_announcement(announcement_id)
        except BigQueryError as exc:
            if "no row matched" in str(exc):
                raise HTTPException(status_code=404, detail="Not found")
            logger.exception("BQ delete failed")
            raise HTTPException(status_code=500, detail="Database error")

    return app
```

#### `db/bigquery.py` — nowa funkcja `list_announcements`

Do dodania po `delete_announcement` (po linii 368):

```python
def list_announcements(limit: int = 20) -> list[dict]:
    """Return recent announcements ordered by published_at DESC."""
    client = _get_client()
    query = f"""
        SELECT
            announcement_id, url, published_at, title, company, ticker,
            analysis_approved, analysis_score, event_type, priority
        FROM `{_table_ref(client)}`
        ORDER BY published_at DESC
        LIMIT @limit
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("limit", "INT64", limit)]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
    except Exception as exc:
        raise BigQueryError(f"list_announcements failed: {exc}") from exc
    return [dict(row) for row in rows]
```

---

## Code References

- `db/bigquery.py:32-50` — pełny schemat tabeli `announcements`
- `db/bigquery.py:52-76` — inicjalizacja singleton klienta BQ (ADC + quota_project guard)
- `db/bigquery.py:266-311` — `fetch_top_n_for_window()` — wzorzec SELECT do naśladowania
- `db/bigquery.py:352-368` — `delete_announcement()` — istniejąca funkcja DELETE ✅
- `src/exceptions.py:12-13` — `BigQueryError` klasa
- `src/logging_setup.py:8-29` — JSON logger, Cloud Logging compatible
- `main.py:1-14` — krytyczny porządek importów (load_dotenv → logging → db/src)
- `pyproject.toml:8-9` — `fastapi>=0.136.1`, `uvicorn>=0.47.0` zainstalowane
- `.github/workflows/deploy.yml:44-57` — current Jobs deploy pattern
- `tests/test_bigquery.py:16-40` — wzorzec mock BQ
- `tests/test_analyzer.py:23-31` — wzorzec mock Gemini
- `tests/test_scraper.py:117` — wzorzec `patch.dict(os.environ, ...)`

## Architecture Insights

### Wzorzec X-API-Key w FastAPI

`APIKeyHeader` z `fastapi.security` + `Depends()` jako dependency — standardowe podejście, czytelne dla recenzenta certyfikacji. Dependency wstrzyknięte na poziomie routera lub per-endpoint. `/health` bez Depends — publiczny.

### Jeden obraz Docker, dwa entrypointy

Dockerfile CMD pozostaje `python main.py` (dla Jobs). Service używa `--command=uv --args="run,python,api_main.py"` jako override w `gcloud run deploy`. Oba Commands używają identycznej warstwy `uv sync` — żadnych duplikacji.

### `published_at` jako string w response

BigQuery zwraca `TIMESTAMP` jako `datetime` obiekt Python. Pydantic serializes to ISO string automatycznie jeśli pole jest `str` — ale bezpieczniej zadeklarować `datetime` w modelu i pozwolić FastAPI na serializację. Do weryfikacji przy implementacji.

### `--set-secrets` w Service deploy (inaczej niż Jobs)

Jobs nie mają `--set-secrets` w CI — sekrety ustawione manualnie raz. Service ma `--set-secrets` w CI/CD: jest to celowa decyzja — idempotentne, audytowalne, jedyne miejsce gdzie widać konfigurację sekretów Service'u.

## Historical Context (from prior changes)

- `context/archive/2026-06-02-bigquery-schema/reviews/impl-review.md:67` — wzmianka o "FastAPI health endpoints are planned" jako przyszłość; klient BQ singleton thread-safety tam zidentyfikowana
- `context/foundation/test-plan.md:119` — "No auth / API key test" = gap medium, FastAPI admin API (PUL-17) not yet implemented

## Open Questions

1. **`published_at` serialization** — BQ zwraca `datetime`, FastAPI serializes to ISO. Trzeba sprawdzić czy Pydantic model potrzebuje `datetime` czy `str` w `AnnouncementItem`.
2. **Service account roles** — `puls-gpw-runner` ma już BQ + Secret Manager access z Jobs. Czy ma `roles/run.admin` na poziomie projektu potrzebne dla `gcloud run deploy` z CI? Wymaga weryfikacji przed pierwszym deploy.
3. **`--min-instances=0`** — Service będzie cold-startował po braku ruchu. Dla certyfikacji (jednorazowy pokaz) OK. Dla przyszłości: rozważyć `--min-instances=1`.
4. **BIGQUERY_DATASET env var** — przekazywany jako `--set-env-vars` do Service. Weryfikować czy `db/bigquery.py:29` czyta go przy inicjalizacji modułu czy przy każdym call (przy inicjalizacji — więc env var musi być dostępny przed importem).
