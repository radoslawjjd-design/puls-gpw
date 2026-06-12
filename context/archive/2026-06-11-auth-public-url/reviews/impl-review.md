<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Auth + Public URL (PUL-17)

- **Plan**: context/changes/auth-public-url/plan.md
- **Scope**: All Phases (1–5)
- **Date**: 2026-06-12
- **Verdict**: APPROVED (after triage fixes)
- **Findings**: 0 critical, 4 warnings, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS |

## Automated Verification

- `uv run pytest tests/ -q` → 93 passed ✅
- `uv run tach check` → All modules validated ✅

## Findings

### F1 — Date filter silently broken (?from/?to ignored)

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: src/api.py:96-97
- **Detail**: HTML wysyłała ?from=...&to=..., API oczekiwało ?from_dt=...&to_dt=... (brak Query alias). Filtry dat były ignorowane bez błędu.
- **Fix**: Dodano `Query(None, alias="from")` i `Query(None, alias="to")` + import Query.
- **Decision**: FIXED

### F2 — limit bez walidacji zakresu

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/api.py:92
- **Detail**: `limit: int = 20` bez ge=1, le=100. Plan wymagał Query(20, ge=1, le=100).
- **Fix**: Dodano `Query(20, ge=1, le=100)`. Paginacja (page/page_size) przeniesiona do PUL-23.
- **Decision**: FIXED

### F3 — None == None: auth bypass gdy env var nie ustawiona

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/api.py:20, api_main.py
- **Detail**: os.environ.get() zwraca None gdy var nie ustawiona. APIKeyHeader z auto_error=False zwraca None gdy brak nagłówka. None == None → admin access bez klucza.
- **Fix**: Walidacja przy starcie w api_main.py — RuntimeError jeśli ADMIN_API_KEY lub USER_API_KEY nie ustawione.
- **Decision**: FIXED via Fix A

### F4 — Brak loggera w src/api.py

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/api.py
- **Detail**: Wszystkie moduły src/ mają logger = logging.getLogger(__name__). api.py nie logował nic — błędy BQ i auth failures były nieme.
- **Fix**: Dodano `import logging`, `logger = logging.getLogger(__name__)`, `logger.error()` przed HTTPException(500).
- **Decision**: FIXED

### F5 — structured_analysis admina: dict zamiast str (plan drift)

- **Severity**: OBSERVATION
- **Dimension**: Plan Adherence
- **Location**: src/api.py:56
- **Detail**: Plan specyfikował `str | None` (raw JSON) dla AnnouncementAdmin. Implementacja zwraca `dict | None` (parsed). Zmiana była świadoma (fix b2c39fe) — admin i user dostają spójne dane. Testy pokrywają nowe zachowanie.
- **Decision**: ACCEPTED (intentional improvement)

### F6 — colspan hardcoded jako 7 (admin=8 col, user=6 col)

- **Severity**: OBSERVATION
- **Dimension**: Safety & Quality
- **Location**: static/index.html:220,228
- **Detail**: "Brak wyników" i "Błąd" używają colspan="7". Admin ma 8 kolumn, user 6. Wizualnie działa, semantycznie niepoprawne.
- **Decision**: SKIPPED (cosmetic, tracked in PUL-25 UI redesign)

### F7 — BQ error details w HTTP 500 response

- **Severity**: OBSERVATION
- **Dimension**: Safety & Quality
- **Location**: src/api.py:124,133
- **Detail**: str(exc) może zawierać szczegóły BQ (projekt, dataset). Ryzyko niskie (auth wymagana, mały zespół).
- **Decision**: SKIPPED (acceptable for now)
