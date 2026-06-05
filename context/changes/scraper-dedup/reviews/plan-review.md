<!-- PLAN-REVIEW-REPORT -->
# Plan Review: S-01 Scraper Bankier.pl + Dedup BigQuery

- **Plan**: `context/changes/scraper-dedup/plan.md`
- **Mode**: Deep
- **Date**: 2026-06-05
- **Verdict**: REVISE → SOUND (po triage)
- **Findings**: 0 critical, 2 warnings, 1 observation

## Verdicts

| Dimension | Verdict |
|---|---|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | WARNING |
| Blind Spots | WARNING |
| Plan Completeness | PASS |

## Grounding

6/6 paths ✓ (nowe pliki poprawnie oznaczone jako nowe), 5/5 symbols ✓, brief↔plan ✓

## Findings

### F1 — datetime.now() nie mockowany w testach jednostkowych

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — testy stop-condition i window-filter będą flaky bez zamrożenia czasu
- **Dimension**: Blind Spots
- **Location**: Phase 3 — tests/test_scraper.py
- **Detail**: Scraper wewnętrznie woła `datetime.now(ZoneInfo("Europe/Warsaw"))`. Testy zależące od dat relatywnie do "teraz" będą przechodzić dziś, nie jutro — chyba że czas zostanie zamrożony.
- **Fix A ⭐ Zastosowany**: `unittest.mock.patch("src.scraper.datetime")` + `mock_dt.now.return_value = fixed_now` w każdym teście. Wymaga importu datetime jako modułu w scraper.py.
- **Decision**: FIXED via Fix A — dodano specyfikację datetime mocking do Phase 3 planu

### F2 — httpx.get() per-request zamiast httpx.Client() z connection pool

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — nowe TCP+TLS handshake dla każdego z ~30 requestów per run
- **Dimension**: Architectural Fitness
- **Location**: Phase 2 — src/http_client.py
- **Detail**: `httpx.get()` tworzy nowy Client przy każdym wywołaniu. Wzorzec singleton istnieje w `db/bigquery.py` (`_get_client()`) i `oldProjectData/base.py` (`_get_session()`).
- **Fix**: Module-level `httpx.Client` singleton z `_get_http_client()` + double-checked locking.
- **Decision**: FIXED — zaktualizowano Contract w Phase 2 planu

### F3 — page_min_dt=None nie obsłużone w stop-condition

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — jednolinijkowy fix
- **Dimension**: Blind Spots
- **Location**: Critical Implementation Details — stop-condition pseudokod
- **Detail**: `if page_min_dt < cutoff: break` rzuca TypeError gdy `page_min_dt is None` (wszystkie daty nieparsowalne).
- **Fix**: `if page_min_dt is None or page_min_dt < cutoff: break`
- **Decision**: FIXED — zaktualizowano pseudokod w Critical Implementation Details
